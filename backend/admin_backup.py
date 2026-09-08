"""Backup and restore endpoints.

The blueprint stays thin: validation, job bookkeeping and streaming. The engine lives in
backend/backup/, mirroring how statements_api delegates to importer/pipeline.
"""

import logging
import os
import secrets
import shutil
import uuid

from flask import Blueprint, jsonify, request, send_file, session

import config
import db
import jobs
from auth import admin_required
from backup import archive, export, restore
from util import api_error, audit, json_body, rows_json

log = logging.getLogger(__name__)

bp = Blueprint("admin_backup", __name__, url_prefix="/api/admin/backup")

# Artifacts live on the statements volume, not /tmp: they survive a worker recycle, the
# directory is already owned by the unprivileged app user, and nginx can serve them.
BACKUP_DIRNAME = "_backups"
KEEP_BACKUPS = 2
KEEP_PRE_RESTORE = 1
UPLOAD_TTL_SECONDS = 6 * 3600
CONFIRM_PHRASE = "RESTORE"

JOB_FIELDS = ("id, kind, status, phase, progress, message, error_message, filename, size_bytes, "
              "stats, warnings, manifest, parent_job_id, created_by_username, created_at, "
              "updated_at, finished_at")


def artifact_modes():
    """The archive holds every user's transactions, their password hashes and the OpenRouter key
    in plain text, so it is owner-only unless nginx has to read it (see config.USE_X_ACCEL)."""
    return (0o755, 0o644) if config.USE_X_ACCEL else (0o700, 0o600)


def backups_dir():
    path = os.path.join(config.STATEMENTS_DIR, BACKUP_DIRNAME)
    dir_mode, _ = artifact_modes()
    os.makedirs(path, mode=dir_mode, exist_ok=True)
    try:
        os.chmod(path, dir_mode)
    except OSError:
        pass
    return path


def _job_json(row):
    out = dict(rows_json([row])[0])
    out["downloadable"] = bool(row.get("filename")) and row["status"] == "done"
    return out


def _load_job(job_id):
    return db.query(f"SELECT {JOB_FIELDS}, file_path, token FROM backup_jobs WHERE id = %s",
                    (job_id,), one=True)


def _active_job(kind=None):
    if kind:
        return db.query("SELECT id FROM backup_jobs WHERE status IN ('queued','running') AND kind = %s LIMIT 1",
                        (kind,), one=True)
    return db.query("SELECT id FROM backup_jobs WHERE status IN ('queued','running') LIMIT 1", one=True)


def _free_space_ok():
    """Refuse with a concrete number rather than filling the volume."""
    try:
        usage = shutil.disk_usage(config.STATEMENTS_DIR)
    except OSError:
        return True, None
    row = db.query("SELECT COALESCE(SUM(file_size), 0) AS b FROM statements", one=True) or {"b": 0}
    size = db.query("SELECT pg_database_size(current_database()) AS b", one=True) or {"b": 0}
    need = int(row["b"]) + int(size["b"]) // 4
    if usage.free < need * 1.2:
        return False, (need, usage.free)
    return True, None


def _human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _prune():
    for kind, keep in (("backup", KEEP_BACKUPS), ("pre_restore", KEEP_PRE_RESTORE)):
        rows = db.query(
            "SELECT id, file_path FROM backup_jobs WHERE kind = %s AND file_path IS NOT NULL "
            "ORDER BY created_at DESC OFFSET %s", (kind, keep)) or []
        for row in rows:
            _drop_artifact(row["id"], row["file_path"])
    # Staged uploads that were never restored.
    import time
    now = time.time()
    for name in os.listdir(backups_dir()):
        if not name.startswith("upload-"):
            continue
        path = os.path.join(backups_dir(), name)
        try:
            if now - os.path.getmtime(path) > UPLOAD_TTL_SECONDS:
                os.unlink(path)
        except OSError:
            pass


def _drop_artifact(job_id, file_path):
    if file_path:
        try:
            os.unlink(file_path)
        except OSError:
            pass
    db.execute("UPDATE backup_jobs SET file_path = NULL, filename = NULL WHERE id = %s", (job_id,))


# ---------- Backup ----------

def _run_backup(job_id, actor_id, kind="backup"):
    from backup.progress import Progress
    prog = Progress(job_id)
    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = "ispend-pre-restore" if kind == "pre_restore" else "ispend-backup"
    filename = f"{prefix}-{os.uname().nodename}-{stamp}.zip"
    path = os.path.join(backups_dir(), filename)
    part = path + ".part"
    try:
        prog.phase("database", 0.02, "Starting")
        _, file_mode = artifact_modes()
        fd = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, file_mode)
        os.close(fd)
        manifest = export.export_archive(part, progress=prog)
        os.replace(part, path)
        size = os.path.getsize(path)
        prog.done(filename=filename, file_path=path, size_bytes=size, manifest=manifest,
                  stats={"tables": {t["name"]: t["rows"] for t in manifest["tables"]},
                         "files": manifest["files"]},
                  warnings=manifest.get("warnings") or [])
        audit("admin.backup.create", {"job_id": job_id, "filename": filename, "size": size},
              user_id=actor_id)
        _prune()
    except Exception as e:
        log.exception("backup job %s failed", job_id)
        prog.failed(str(e))
        try:
            os.unlink(part)
        except OSError:
            pass
    finally:
        prog.close()


@bp.get("")
@admin_required
def list_jobs():
    rows = db.query(f"SELECT {JOB_FIELDS}, file_path IS NOT NULL AS has_file FROM backup_jobs "
                    "ORDER BY created_at DESC LIMIT 25") or []
    return jsonify([_job_json(r) for r in rows])


@bp.post("")
@admin_required
def start_backup():
    if _active_job():
        return api_error("A backup or restore is already running", 409)
    ok, need = _free_space_ok()
    if not ok:
        return api_error(f"Not enough free disk space (need about {_human(need[0])}, "
                         f"{_human(need[1])} free)", 507)
    row = db.execute(
        """INSERT INTO backup_jobs (kind, created_by, created_by_username)
           VALUES ('backup', %s, %s) RETURNING id""",
        (session["user_id"], session.get("username")), returning=True)
    jobs.spawn(_run_backup, row["id"], session["user_id"])
    return jsonify({"id": row["id"], "kind": "backup", "status": "queued"}), 202


@bp.get("/<int:job_id>")
@admin_required
def job_status(job_id):
    row = _load_job(job_id)
    if not row:
        return api_error("Not found", 404)
    return jsonify(_job_json(row))


@bp.get("/<int:job_id>/download")
@admin_required
def download(job_id):
    row = _load_job(job_id)
    if not row or not row.get("file_path") or not os.path.isfile(row["file_path"]):
        return api_error("This backup is no longer available", 404)
    audit("admin.backup.download", {"job_id": job_id, "filename": row["filename"]})
    if config.USE_X_ACCEL:
        # nginx serves the bytes; the gunicorn thread is released immediately.
        response = jsonify({})
        response.headers["X-Accel-Redirect"] = f"/protected/{BACKUP_DIRNAME}/{row['filename']}"
        response.headers["Content-Type"] = "application/zip"
        response.headers["Content-Disposition"] = f'attachment; filename="{row["filename"]}"'
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    return send_file(row["file_path"], as_attachment=True, download_name=row["filename"],
                     mimetype="application/zip", conditional=True)


@bp.delete("/<int:job_id>")
@admin_required
def delete_job(job_id):
    row = _load_job(job_id)
    if not row:
        return api_error("Not found", 404)
    _drop_artifact(job_id, row.get("file_path"))
    db.execute("DELETE FROM backup_jobs WHERE id = %s", (job_id,))
    audit("admin.backup.delete", {"job_id": job_id})
    return jsonify({"ok": True})


# ---------- Restore ----------

def _staged_path(upload_id):
    """Reject anything that is not one of our own generated names before touching the disk."""
    try:
        uuid.UUID(upload_id)
    except (ValueError, AttributeError, TypeError):
        return None
    return os.path.join(backups_dir(), f"upload-{upload_id}.zip")


@bp.post("/upload", strict_slashes=False)
@admin_required
def upload_archive():
    """Raw application/octet-stream body streamed to disk, so a multi-GB upload never lands in
    memory and no multipart parser is involved. Mutates nothing: this is the inspect step."""
    upload_id = str(uuid.uuid4())
    path = _staged_path(upload_id)
    total = 0
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = request.stream.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > config.MAX_RESTORE_BYTES:
                    raise archive.ArchiveError(
                        f"That archive is larger than the {_human(config.MAX_RESTORE_BYTES)} limit.")
                out.write(chunk)
        if not total:
            raise archive.ArchiveError("No file was uploaded.")
        report = restore.inspect(path)
    except archive.ArchiveError as e:
        os.unlink(path)
        return api_error(str(e), 400)
    except Exception:
        os.unlink(path)
        raise
    audit("admin.restore.inspect", {"upload_id": upload_id, "size": total,
                                    "compatible": report["compatible"]})
    return jsonify({"upload_id": upload_id, "size_bytes": total, **report})


def _run_restore(job_id, upload_id, actor_id, delete_extra, fast):
    from backup.progress import Progress
    prog = Progress(job_id)
    path = _staged_path(upload_id)
    try:
        # A safety backup first: the admin can always get back to where they were.
        prog.phase("safety-backup", 0.01, "Backing up the current data first")
        safety = db.execute(
            """INSERT INTO backup_jobs (kind, status, created_by, created_by_username, parent_job_id)
               VALUES ('pre_restore', 'running', %s, %s, %s) RETURNING id""",
            (actor_id, None, job_id), returning=True)
        _run_backup(safety["id"], actor_id, kind="pre_restore")
        db.execute("UPDATE backup_jobs SET parent_job_id = %s WHERE id = %s", (safety["id"], job_id))

        warnings = restore.restore_archive(path, progress=prog, delete_extra_files=delete_extra,
                                           fast=fast)
        prog.done(warnings=warnings, stats={"safety_backup_job_id": safety["id"]})
        audit("admin.restore.complete", {"job_id": job_id, "warnings": len(warnings)},
              user_id=actor_id)
        try:
            os.unlink(path)
        except OSError:
            pass
    except Exception as e:
        log.exception("restore job %s failed", job_id)
        prog.failed(str(e))
        audit("admin.restore.failed", {"job_id": job_id, "error": str(e)[:500]}, user_id=actor_id)
    finally:
        prog.close()


@bp.post("/restore", strict_slashes=False)
@admin_required
def start_restore():
    data = json_body()
    if data.get("confirm") != CONFIRM_PHRASE:
        return api_error(f"Type {CONFIRM_PHRASE} to confirm", 400)
    upload_id = data.get("upload_id")
    path = _staged_path(upload_id)
    if not path or not os.path.isfile(path):
        return api_error("That upload has expired. Upload the archive again.", 404)
    if _active_job():
        return api_error("A backup or restore is already running", 409)
    busy = db.query(
        """SELECT 1 FROM statements WHERE status IN ('parsing','committing')
             AND updated_at > now() - interval '10 minutes' LIMIT 1""", one=True)
    if busy:
        return api_error("A statement is being imported. Wait for it to finish.", 409)
    token = secrets.token_urlsafe(32)
    row = db.execute(
        """INSERT INTO backup_jobs (kind, token, created_by, created_by_username)
           VALUES ('restore', %s, %s, %s) RETURNING id""",
        (token, session["user_id"], session.get("username")), returning=True)
    audit("admin.restore.start", {"job_id": row["id"], "upload_id": upload_id})
    jobs.spawn(_run_restore, row["id"], upload_id, session["user_id"],
               bool(data.get("delete_extra_files")), bool(data.get("fast")))
    return jsonify({"id": row["id"], "token": token, "status": "queued"}), 202


@bp.get("/restore/<int:job_id>")
def restore_status(job_id):
    """Deliberately NOT @admin_required: a restore replaces the users table, so the admin who
    started it is signed out partway through and could otherwise never see the outcome. The
    one-time token issued when the restore started stands in for the session."""
    row = _load_job(job_id)
    if not row or row["kind"] != "restore":
        return api_error("Not found", 404)
    token = request.args.get("token")
    authorised = session.get("role") == "admin" or (row.get("token") and token == row["token"])
    if not authorised:
        return api_error("Not found", 404)   # never confirm the job exists to an unauthorised caller
    return jsonify(_job_json(row))
