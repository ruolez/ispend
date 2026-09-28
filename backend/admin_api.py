"""Admin console: user lifecycle, instance statistics and the activity log.

Every view here is @admin_required. The user endpoints moved out of settings_api because
DELETE changed meaning (it used to deactivate, it now soft-deletes and hides the user) --
moving the path makes the old URL 404 loudly instead of silently doing something different.
"""

import json
import os
import shutil

from flask import Blueprint, jsonify, request, session
from werkzeug.security import generate_password_hash

import admin_privacy
import auth
import config
import db
import entitlement
import seed_categories
import user_state
from auth import MIN_PASSWORD_LEN, admin_required, password_problem, step_up_missing, step_up_required
from util import (admin_audit_retention_days, api_error, audit, audit_retention_days, csv_response, iso,
                  json_body, rows_json, to_int)

bp = Blueprint("admin", __name__, url_prefix="/api/admin")

DEFAULT_RETENTION_DAYS = 30


def _uid():
    return session["user_id"]


def _not_self(user_id, verb):
    if user_id == _uid():
        return api_error(f"You cannot {verb} your own account")
    return None


def _load(user_id):
    return db.query(
        """SELECT id, username, role, status, locked_at, lock_reason, deleted_at FROM users
            WHERE id = %s AND role = 'user'""",
        (user_id,), one=True)


# Guards every write that could remove the last way into the admin console. Written as one
# statement rather than a read-then-write because two concurrent demotions would both pass a
# separate check and leave the instance with no admin.
_LAST_ADMIN_GUARD = """
  AND (role <> 'admin' OR EXISTS (
        SELECT 1 FROM users WHERE role = 'admin' AND status = 'active' AND id <> %(uid)s))
"""
LAST_ADMIN_ERROR = "There must always be at least one active administrator"


def _retention_days():
    raw = db.get_setting("admin_deleted_user_retention_days")
    try:
        return max(0, min(3650, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


# ---------- Step-up ----------

@bp.get("/step-up/status")
@admin_required
def step_up_status():
    return jsonify(auth.step_up_status())


@bp.post("/step-up")
@admin_required
def step_up():
    return auth.step_up(json_body().get("password"))


# ---------- Search (the admin command palette) ----------

@bp.get("/search")
@admin_required
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"users": []})
    uid = int(q.lstrip("#")) if q.lstrip("#").isdigit() else None
    rows = db.query(
        """SELECT u.id, u.username, u.email, u.status, u.role, s.ent_state
             FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id
            WHERE u.role = 'user' AND (u.id = %(id)s OR u.username ILIKE %(like)s OR u.email ILIKE %(like)s)
            ORDER BY (u.id = %(id)s) DESC NULLS LAST, (lower(u.email) = lower(%(q)s)) DESC NULLS LAST,
                     u.last_seen_at DESC NULLS LAST, u.id
            LIMIT 8""",
        {"id": uid, "like": f"%{q}%", "q": q}) or []
    return jsonify({"users": rows_json(rows)})


# ---------- Users ----------

@bp.post("/users")
@admin_required
def create_user():
    import admin_users
    data = json_body()
    email = (data.get("email") or "").strip().lower() or None
    username = (data.get("username") or "").strip() or (email or "")
    invite = bool(data.get("send_invite"))
    password = admin_users.random_password() if invite else (data.get("password") or "")
    access = data.get("access") or "trial"
    if access not in ("trial", "comped"):
        return api_error("access must be trial or comped")
    if email and auth.email_problem(email):
        return api_error(auth.email_problem(email))
    if invite and not email:
        return api_error("An invitation needs an email address")
    if not username or password_problem(password):
        return api_error(f"Username and a password of at least {MIN_PASSWORD_LEN} characters are required")
    if email and auth.identity_taken(email):
        return jsonify({"error": "That email is already in use.", "code": "email_taken"}), 409
    existing = db.query("SELECT id, status, username FROM users WHERE username = %s", (username,), one=True)
    if existing and existing["status"] == user_state.DELETED:
        # A deleted user keeps their username reserved (renaming would break restore and poison
        # the audit trail), so turn the dead end into the action the admin actually wants.
        return jsonify({"error": f"“{username}” was deleted and can be restored.",
                        "code": "username_deleted", "user_id": existing["id"]}), 409
    if existing:
        return api_error("Username already exists")
    with db.transaction():
        row = db.execute(
            "INSERT INTO users (username, email, password_hash, role) VALUES (%s, %s, %s, 'user') RETURNING id",
            (username, email, generate_password_hash(password)),
            returning=True, commit=False,
        )
        # Without a row the evaluator would quietly start a trial from created_at; writing it
        # makes the choice explicit and visible in the billing columns.
        if access == "comped":
            db.execute("""INSERT INTO subscriptions (user_id, status, comped_until)
                          VALUES (%s, 'comped', 'infinity')""", (row["id"],), commit=False)
        else:
            db.execute("""INSERT INTO subscriptions (user_id, status, trial_end)
                          VALUES (%s, 'trialing', now() + make_interval(days => %s))""",
                       (row["id"], entitlement.trial_days()), commit=False)
        seed_categories.seed_for_user(db.get_db(), row["id"])
    import ledger
    ledger.record_admin(row["id"], "comped" if access == "comped" else "trial_started",
                        {"created_by_admin": True})
    ledger.refresh_ent_state(row["id"])
    audit("user.create", {"id": row["id"], "username": username, "access": access,
                          "invited": invite}, target=row["id"])
    if invite:
        admin_users._invite({"id": row["id"], "email": email, "username": username})
    return jsonify({"id": row["id"], "invited": invite}), 201


@bp.put("/users/<int:user_id>/password")
@admin_required
@step_up_required
def reset_password(user_id):
    data = json_body()
    password = data.get("password") or ""
    problem = password_problem(password)
    if problem:
        return api_error(problem)
    if not _load(user_id):
        return api_error("User not found", 404)
    # Bumping the epoch signs out every session the old password opened, the same as a
    # self-service change or an emailed reset.
    db.execute("""UPDATE users SET password_hash = %s, session_epoch = session_epoch + 1,
                                   updated_at = now()
                   WHERE id = %s""", (generate_password_hash(password), user_id))
    audit("user.password_reset", {"id": user_id}, target=user_id)
    return jsonify({"ok": True})


@bp.post("/users/<int:user_id>/lock")
@admin_required
def lock_user(user_id):
    blocked = _not_self(user_id, "lock")
    if blocked:
        return blocked
    row = _load(user_id)
    if not row:
        return api_error("User not found", 404)
    if row["status"] == user_state.DELETED:
        return api_error("That user is in the trash. Restore them first.", 409)
    reason = (json_body().get("reason") or "").strip()[:200] or None
    n = db.execute(
        """UPDATE users SET status = 'locked', locked_at = now(), lock_reason = %(reason)s,
                            updated_at = now()
            WHERE id = %(id)s""" + _LAST_ADMIN_GUARD,
        {"id": user_id, "uid": user_id, "reason": reason})
    if not n:
        return api_error(LAST_ADMIN_ERROR, 409)
    audit("user.lock", {"id": user_id, "username": row["username"], "reason": reason}, target=user_id)
    return jsonify({"ok": True, "status": user_state.LOCKED})


@bp.post("/users/<int:user_id>/unlock")
@admin_required
def unlock_user(user_id):
    row = _load(user_id)
    if not row:
        return api_error("User not found", 404)
    if row["status"] != user_state.LOCKED:
        return api_error("That user is not locked", 409)
    db.execute(
        "UPDATE users SET status = 'active', locked_at = NULL, lock_reason = NULL, updated_at = now() WHERE id = %s",
        (user_id,))
    audit("user.unlock", {"id": user_id, "username": row["username"]}, target=user_id)
    return jsonify({"ok": True, "status": user_state.ACTIVE})


@bp.post("/users/<int:user_id>/restore")
@admin_required
def restore_user(user_id):
    row = _load(user_id)
    if not row:
        return api_error("User not found", 404)
    if row["status"] != user_state.DELETED:
        return api_error("That user is not in the trash", 409)
    want = json_body().get("to")
    target = user_state.ACTIVE if want == user_state.ACTIVE else user_state.restore_target(row)
    db.execute(
        """UPDATE users SET status = %s, deleted_at = NULL, deleted_by = NULL,
                            locked_at = CASE WHEN %s = 'locked' THEN locked_at ELSE NULL END,
                            lock_reason = CASE WHEN %s = 'locked' THEN lock_reason ELSE NULL END,
                            updated_at = now()
            WHERE id = %s""",
        (target, target, target, user_id))
    audit("user.restore", {"id": user_id, "username": row["username"], "status": target}, target=user_id)
    return jsonify({"ok": True, "status": target})


@bp.delete("/users/<int:user_id>")
@admin_required
def delete_user(user_id):
    permanent = request.args.get("permanent") == "true"
    blocked = (permanent and step_up_missing()) or _not_self(user_id, "delete")
    if blocked:
        return blocked
    row = _load(user_id)
    if not row:
        return api_error("User not found", 404)
    if permanent:
        return _purge(row)
    if row["status"] == user_state.DELETED:
        return api_error("That user is already in the trash", 409)
    n = db.execute(
        """UPDATE users SET status = 'deleted', deleted_at = now(), deleted_by = %(actor)s,
                            updated_at = now()
            WHERE id = %(id)s""" + _LAST_ADMIN_GUARD,
        {"id": user_id, "uid": user_id, "actor": _uid()})
    if not n:
        return api_error(LAST_ADMIN_ERROR, 409)
    audit("user.delete", {"id": user_id, "username": row["username"]}, target=user_id)
    return jsonify({"ok": True, "status": user_state.DELETED})


def _purge(row):
    """Irreversible. Only reachable from 'deleted', so destroying a person's financial records
    always takes two deliberate actions separated in time."""
    user_id = row["id"]
    if row["status"] != user_state.DELETED:
        return api_error("Move the user to the trash first, then delete permanently.", 409)
    if request.args.get("confirm") != row["username"]:
        return api_error("Type the username to confirm", 409)
    busy = db.query(
        """SELECT 1 FROM statements
            WHERE user_id = %s AND status IN ('parsing','committing')
              AND updated_at > now() - interval '10 minutes' LIMIT 1""",
        (user_id,), one=True)
    if busy:
        return api_error("An import is still running for this user. Try again in a few minutes.", 409)
    counts = db.query("SELECT COUNT(*) AS n FROM transactions WHERE user_id = %s", (user_id,), one=True)
    with db.transaction():
        db.execute("DELETE FROM users WHERE id = %s", (user_id,), commit=False)
        db.execute("DELETE FROM settings WHERE key LIKE %s", (f"u{user_id}:%",), commit=False)
    shutil.rmtree(os.path.join(config.STATEMENTS_DIR, str(user_id)), ignore_errors=True)
    # audit_log.user_id goes NULL on delete, so the username has to live in the detail or the
    # trail becomes unreadable exactly when it matters.
    audit("user.purge", {"id": user_id, "username": row["username"], "txn_count": counts["n"]})
    return jsonify({"ok": True, "purged": True})


# ---------- Statistics ----------

def _dir_bytes(path):
    total = 0
    with os.scandir(path) as it:
        for e in it:
            if e.is_file(follow_symlinks=False):
                total += e.stat().st_size
    return total


def _disk_usage(user_id=None):
    """The truth about storage. statements.file_size over-counts (the same sha uploaded twice is
    two rows but one file) and under-counts (OCR output has no size column anywhere), so the DB
    can only ever approximate this. Never let an unmounted volume 500 the dashboard."""
    root = config.STATEMENTS_DIR
    try:
        if user_id is not None:
            return _dir_bytes(os.path.join(root, str(user_id))), True
        total = 0
        with os.scandir(root) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False) and entry.name.isdigit():
                    total += _dir_bytes(entry.path)
        return total, True
    except OSError:
        return None, False


_SOURCE_BYTES_SQL = """
SELECT COALESCE(SUM(file_size), 0) AS source_bytes, COUNT(*) AS unique_files
  FROM (SELECT DISTINCT ON (user_id, file_sha256) user_id, file_size
          FROM statements WHERE status <> 'discarded' {where}
         ORDER BY user_id, file_sha256, id) s
"""

# ---------- Activity log ----------

_AUDIT_SQL = """
SELECT a.id, a.user_id, u.username, a.action, a.detail, a.by_admin, a.target_user_id,
       t.username AS target_username, a.ip::text AS ip, a.created_at
  FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
  LEFT JOIN users t ON t.id = a.target_user_id
 WHERE (%(cursor)s IS NULL OR a.id < %(cursor)s)
   AND (%(user_id)s IS NULL OR a.user_id = %(user_id)s OR a.target_user_id = %(user_id)s)
   AND (%(admin_only)s IS FALSE OR a.by_admin)
   AND (%(action)s IS NULL OR a.action = %(action)s)
   AND (%(from)s IS NULL OR a.created_at >= %(from)s::timestamptz)
   AND (%(to)s IS NULL OR a.created_at < %(to)s::timestamptz)
   AND (%(q)s IS NULL OR a.action ILIKE %(q)s OR a.detail::text ILIKE %(q)s)
 ORDER BY a.id DESC
 LIMIT %(limit)s
"""


@bp.get("/audit")
@admin_required
def list_audit():
    csv_export = request.args.get("format") == "csv"
    if csv_export:
        blocked = step_up_missing()
        if blocked:
            return blocked
    q = (request.args.get("q") or "").strip()
    limit = to_int(request.args.get("limit"), "limit", lo=1, hi=500) or 50
    params = {
        "cursor": to_int(request.args.get("cursor"), "cursor"),
        "user_id": to_int(request.args.get("user_id"), "user_id"),
        "action": request.args.get("action") or None,
        "from": request.args.get("from") or None,
        "to": request.args.get("to") or None,
        "q": f"%{q}%" if q else None,
        "admin_only": request.args.get("admin") == "1",
        "limit": 10000 if csv_export else limit,
    }
    rows = [admin_privacy.redact_row(r) for r in rows_json(db.query(_AUDIT_SQL, params) or [])]
    if csv_export:
        audit("admin.audit.export", {"rows": len(rows)})
        flat = [{**r, "detail": json.dumps(r["detail"]) if r["detail"] is not None else ""} for r in rows]
        return csv_response(flat, "activity.csv", ["created_at", "id", "user_id", "username", "action",
                                                   "target_user_id", "target_username", "by_admin", "ip",
                                                   "detail"])
    return jsonify({"items": rows, "next_cursor": rows[-1]["id"] if len(rows) == limit else None})


@bp.get("/audit/actions")
@admin_required
def audit_actions():
    return jsonify(rows_json(db.query(
        "SELECT action, COUNT(*) AS n FROM audit_log GROUP BY action ORDER BY n DESC") or []))


# ---------- Housekeeping settings ----------

ADMIN_SETTINGS = {
    "audit_retention_days": (7, 3650),
    "admin_audit_retention_days": (7, 3650),
    "admin_deleted_user_retention_days": (0, 3650),
}


@bp.get("/settings")
@admin_required
def get_admin_settings():
    house = db.query("SELECT COUNT(*) AS audit_rows, MIN(created_at) AS oldest_audit_at FROM audit_log",
                     one=True) or {}
    return jsonify({"audit_retention_days": audit_retention_days(),
                    "admin_audit_retention_days": admin_audit_retention_days(),
                    "deleted_user_retention_days": _retention_days(),
                    "audit_rows": house.get("audit_rows", 0),
                    "oldest_audit_at": iso(house.get("oldest_audit_at"))})


@bp.put("/settings")
@admin_required
def put_admin_settings():
    data = json_body()
    aliases = {"deleted_user_retention_days": "admin_deleted_user_retention_days"}
    changed = []
    for key, value in data.items():
        setting = aliases.get(key, key)
        if setting not in ADMIN_SETTINGS:
            continue
        lo, hi = ADMIN_SETTINGS[setting]
        n = to_int(value, key, lo=lo, hi=hi, required=True)
        db.set_setting(setting, str(n))
        changed.append(key)
    if changed:
        audit("settings.admin_update", {"fields": changed})
    return jsonify({"ok": True})


# ---------- The admin's own account ----------

WIPE_CONFIRM = "delete"


@bp.get("/me/leftover-data")
@admin_required
def leftover_data():
    """Finance data this admin account holds from before admin accounts stopped using the app."""
    import privacy
    counts = privacy.owned_data_counts(_uid())
    return jsonify({"counts": counts, "any": any(counts.values())})


@bp.post("/me/leftover-data/wipe")
@admin_required
@step_up_required
def wipe_leftover_data():
    import privacy
    if (json_body().get("confirm") or "").strip().lower() != WIPE_CONFIRM:
        return api_error(f"Type {WIPE_CONFIRM} to confirm", 409)
    try:
        counts = privacy.wipe_owned_data(_uid())
    except privacy.EraseError as e:
        return api_error(str(e), e.status)
    audit("admin.leftover_data.wipe", counts)
    return jsonify({"ok": True, "removed": counts})


# ---------- Instance AI key ----------
# The key and model every customer without their own use. Customers set their own in the app.

AI_MASK = "••••••••"


def _ai_config():
    return {"api_key": AI_MASK if db.get_setting("openrouter_api_key") else "",
            "model": db.get_setting("openrouter_model") or ""}


@bp.get("/ai-config")
@admin_required
def get_ai_config():
    return jsonify(_ai_config())


@bp.put("/ai-config")
@admin_required
def put_ai_config():
    data = json_body()
    key = data.get("api_key")
    if key is not None and key != AI_MASK:
        blocked = step_up_missing()
        if blocked:
            return blocked
        db.set_setting("openrouter_api_key", str(key).strip())
    if "model" in data:
        db.set_setting("openrouter_model", str(data.get("model") or "").strip()[:200])
    audit("settings.ai_shared", {"key_changed": key is not None and key != AI_MASK, "model": "model" in data})
    return jsonify(_ai_config())


@bp.get("/ai-config/models")
@admin_required
def ai_models():
    import openrouter
    try:
        return jsonify(openrouter.list_models(force=request.args.get("refresh") == "1"))
    except openrouter.OpenRouterError as e:
        return api_error(str(e), 502)


@bp.post("/ai-config/test")
@admin_required
def ai_test():
    import openrouter
    data = json_body()
    key = (data.get("api_key") or "").strip()
    try:
        return jsonify(openrouter.test_connection(key=None if key in ("", AI_MASK) else key,
                                                  model_id=(data.get("model") or "").strip() or None))
    except openrouter.OpenRouterError as e:
        return api_error(str(e))
