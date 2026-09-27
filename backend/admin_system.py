"""Admin › System and Imports & AI: is the machinery working, and what does it cost.

Health is one call with a section per moving part — imports, email, Stripe, backups, storage and
server errors — each with a status the page turns into a green/amber/red chip and the details
behind it. Like everything in the admin, it reports counts and states: error messages shown for
imports are the generic user-facing ones, never file names.
"""

import shutil
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

import admin_metrics
import admin_range
import config
import db
from auth import admin_required
from util import api_error, audit, iso, rows_json

bp = Blueprint("admin_system", __name__, url_prefix="/api/admin")

STUCK_MINUTES = 15
DISK_CACHE_SECONDS = 3600
DISK_LOW_FRACTION = 0.1
BACKUP_STALE_DAYS = 7
STOPPED_MESSAGE = ("This import was stopped by an administrator because it had been running for too long. "
                   "Upload the file again to retry.")

OK, WARN, ERROR = "ok", "warn", "error"


def _worst(*states):
    return ERROR if ERROR in states else (WARN if WARN in states else OK)


def imports_health():
    stuck = db.query(
        """SELECT id, user_id, status, COALESCE(bank_profile, 'unknown') AS bank_profile, file_kind,
                  EXTRACT(epoch FROM now() - updated_at)::int / 60 AS age_min
             FROM statements
            WHERE status IN ('parsing', 'committing') AND updated_at < now() - make_interval(mins => %s)
            ORDER BY updated_at LIMIT 20""", (STUCK_MINUTES,)) or []
    counts = db.query(
        """SELECT COUNT(*) FILTER (WHERE status = 'error' AND updated_at > now() - interval '24 hours') AS failed_24h,
                  COUNT(*) FILTER (WHERE status = 'error' AND updated_at > now() - interval '7 days') AS failed_7d,
                  COUNT(*) FILTER (WHERE created_at > now() - interval '7 days') AS uploaded_7d,
                  COUNT(*) FILTER (WHERE status IN ('parsing', 'committing')) AS running
             FROM statements""", one=True) or {}
    failed_7d, uploaded_7d = int(counts.get("failed_7d") or 0), int(counts.get("uploaded_7d") or 0)
    status = ERROR if stuck else (WARN if uploaded_7d and failed_7d / uploaded_7d > 0.2 else OK)
    return {"status": status, "stuck": rows_json(stuck), **{k: int(v or 0) for k, v in counts.items()}}


def email_health():
    import mailer
    row = db.query(
        """SELECT COUNT(*) FILTER (WHERE status = 'sent' AND created_at > now() - interval '24 hours') AS sent_24h,
                  COUNT(*) FILTER (WHERE status = 'failed' AND created_at > now() - interval '24 hours') AS failed_24h,
                  COUNT(*) FILTER (WHERE status = 'skipped' AND created_at > now() - interval '24 hours') AS skipped_24h,
                  COUNT(*) FILTER (WHERE status = 'queued' AND created_at < now() - interval '10 minutes') AS stuck
             FROM email_log""", one=True) or {}
    last = db.query("""SELECT created_at AS at, template, error FROM email_log
                        WHERE status = 'failed' ORDER BY id DESC LIMIT 1""", one=True)
    by_template = db.query(
        """SELECT template, COUNT(*) FILTER (WHERE status = 'sent') AS sent,
                  COUNT(*) FILTER (WHERE status = 'failed') AS failed, COUNT(*) FILTER (WHERE status = 'skipped') AS skipped
             FROM email_log WHERE created_at > now() - interval '7 days' GROUP BY 1 ORDER BY COUNT(*) DESC""") or []
    configured = mailer.configured()
    counts = {k: int(v or 0) for k, v in row.items()}
    status = WARN if not configured else (ERROR if counts["failed_24h"] > counts["sent_24h"] and counts["failed_24h"]
                                          else (WARN if counts["failed_24h"] or counts["stuck"] else OK))
    return {"status": status, "configured": configured, **counts,
            "last_failure": rows_json([last])[0] if last else None, "by_template": rows_json(by_template)}


def stripe_health():
    import billing
    import billing_reconcile
    enabled = billing.enabled()
    row = db.query(
        """SELECT MAX(received_at) AS last_event_at,
                  COUNT(*) FILTER (WHERE received_at > now() - interval '24 hours') AS events_24h,
                  COUNT(*) FILTER (WHERE status = 'failed' AND received_at > now() - interval '7 days') AS failed_7d,
                  COUNT(*) FILTER (WHERE status = 'ignored' AND received_at > now() - interval '7 days') AS ignored_7d,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY processing_ms)
                    FILTER (WHERE processing_ms IS NOT NULL AND received_at > now() - interval '7 days') AS p95_ms
             FROM stripe_events""", one=True) or {}
    failed = db.query("""SELECT id, type, error, received_at FROM stripe_events WHERE status = 'failed'
                          ORDER BY received_at DESC LIMIT 5""") or []
    last = billing_reconcile.last_result()
    failed_7d = int(row.get("failed_7d") or 0)
    status = OK if not enabled else (ERROR if failed_7d else (WARN if last and last.get("error") else OK))
    return {"status": status, "enabled": enabled, "last_event_at": iso(row.get("last_event_at")),
            "events_24h": int(row.get("events_24h") or 0), "failed_7d": failed_7d,
            "ignored_7d": int(row.get("ignored_7d") or 0),
            "p95_ms": None if row.get("p95_ms") is None else int(row["p95_ms"]),
            "recent_failed": rows_json(failed), "reconcile_last": last}


def backup_health():
    ok = db.query("""SELECT finished_at, size_bytes FROM backup_jobs WHERE kind = 'backup' AND status = 'done'
                      ORDER BY finished_at DESC LIMIT 1""", one=True)
    bad = db.query("""SELECT finished_at, error_message FROM backup_jobs WHERE kind = 'backup' AND status = 'error'
                       ORDER BY finished_at DESC NULLS LAST LIMIT 1""", one=True)
    stale = not ok or ok.get("finished_at") is None or \
        ok["finished_at"] < datetime.now(timezone.utc) - timedelta(days=BACKUP_STALE_DAYS)
    failed_after = bool(bad and ok and bad.get("finished_at") and ok.get("finished_at")
                        and bad["finished_at"] > ok["finished_at"])
    return {"status": ERROR if failed_after else (WARN if stale else OK),
            "last_success_at": ok["finished_at"].isoformat() if ok and ok.get("finished_at") else None,
            "size_bytes": int(ok["size_bytes"] or 0) if ok else None,
            "last_error_at": bad["finished_at"].isoformat() if bad and bad.get("finished_at") else None,
            "last_error": (bad or {}).get("error_message")}


def _disk():
    import admin_api
    used, readable = admin_api._disk_usage()
    try:
        total, _used, free = shutil.disk_usage(config.STATEMENTS_DIR)
    except OSError:
        total = free = None
    return {"disk_bytes": used, "disk_scan_ok": readable, "volume_total": total, "volume_free": free}


def storage_health(refresh=False):
    import admin_api
    disk = admin_metrics.cached("system:disk", _disk, refresh=refresh, ttl=DISK_CACHE_SECONDS)
    src = db.query(admin_api._SOURCE_BYTES_SQL.format(where=""), one=True) or {}
    size = db.query("SELECT pg_database_size(current_database()) AS n", one=True) or {}
    tables = db.query(
        """SELECT c.relname AS name, pg_total_relation_size(c.oid) AS bytes, GREATEST(c.reltuples, 0)::bigint AS rows_est
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema() AND c.relkind = 'r'
            ORDER BY 2 DESC LIMIT 12""") or []
    total, free = disk.get("volume_total"), disk.get("volume_free")
    status = WARN if total and free is not None and free / total < DISK_LOW_FRACTION else OK
    return {"status": status, **disk, "source_bytes": int(src.get("source_bytes") or 0),
            "unique_files": int(src.get("unique_files") or 0), "db_bytes": int(size.get("n") or 0),
            "tables": rows_json(tables)}


def errors_health():
    row = db.query("""SELECT COUNT(*) FILTER (WHERE created_at > now() - interval '24 hours') AS count_24h,
                             COUNT(*) AS count_7d
                        FROM app_errors WHERE created_at > now() - interval '7 days'""", one=True) or {}
    groups = db.query(
        """SELECT fingerprint, MAX(error_type) AS error_type, MAX(location) AS location, COUNT(*) AS n,
                  MAX(created_at) AS last_at, (ARRAY_AGG(message ORDER BY id DESC))[1] AS message,
                  (ARRAY_AGG(source ORDER BY id DESC))[1] AS source
             FROM app_errors WHERE created_at > now() - interval '7 days'
            GROUP BY fingerprint ORDER BY MAX(created_at) DESC LIMIT 20""") or []
    c24 = int(row.get("count_24h") or 0)
    return {"status": WARN if c24 else OK, "count_24h": c24, "count_7d": int(row.get("count_7d") or 0),
            "groups": rows_json(groups)}


@bp.get("/system/health")
@admin_required
def health():
    refresh = request.args.get("refresh") == "1"
    parts = {"imports": imports_health(), "email": email_health(), "stripe": stripe_health(),
             "backups": backup_health(), "storage": storage_health(refresh), "errors": errors_health()}
    return jsonify({**parts, "status": _worst(*(p["status"] for p in parts.values()))})


@bp.get("/system/errors/<fingerprint>")
@admin_required
def error_detail(fingerprint):
    latest = db.query(
        """SELECT id, source, error_type, message, location, status, user_id, traceback, created_at
             FROM app_errors WHERE fingerprint = %s ORDER BY id DESC LIMIT 1""", (fingerprint,), one=True)
    if not latest:
        return api_error("Not found", 404)
    days = db.query(
        """SELECT (created_at AT TIME ZONE %s)::date AS day, COUNT(*) AS n FROM app_errors
            WHERE fingerprint = %s AND created_at > now() - interval '14 days' GROUP BY 1 ORDER BY 1""",
        (config.APP_TIMEZONE, fingerprint)) or []
    return jsonify({"latest": rows_json([latest])[0], "days": rows_json(days)})


@bp.post("/system/imports/<int:statement_id>/fail")
@admin_required
def fail_stuck_import(statement_id):
    """Only an import that has stopped moving: a live one would carry on writing afterwards."""
    row = db.execute(
        """UPDATE statements SET status = 'error', error_message = %s, updated_at = now()
            WHERE id = %s AND status IN ('parsing', 'committing')
              AND updated_at < now() - make_interval(mins => %s)
            RETURNING user_id""", (STOPPED_MESSAGE, statement_id, STUCK_MINUTES), returning=True)
    if not row:
        return api_error("That import is not stuck (it may have finished, or it is still within its first "
                         f"{STUCK_MINUTES} minutes).", 409)
    audit("admin.import.stopped", {"id": statement_id}, target=row["user_id"])
    return jsonify({"ok": True})


# ---------- imports & AI over a period ----------

IMPORTS_BY_PROFILE = """
SELECT COALESCE(bank_profile, 'unknown') AS profile, COUNT(*) AS n,
       COUNT(*) FILTER (WHERE status = 'error') AS errors, COUNT(*) FILTER (WHERE ocr_applied) AS ocr,
       COUNT(*) FILTER (WHERE status = 'committed') AS committed,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY parse_ms) FILTER (WHERE parse_ms IS NOT NULL) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY parse_ms) FILTER (WHERE parse_ms IS NOT NULL) AS p95_ms,
       AVG(profile_confidence) AS confidence
  FROM statements WHERE created_at >= %(a)s AND created_at < %(b)s
 GROUP BY 1 ORDER BY n DESC LIMIT 20
"""


def imports_report(rng):
    b = admin_metrics._bounds(rng)
    bkts = admin_range.buckets(rng["start"], rng["end"], rng["bucket"])
    daily = db.query(
        """SELECT (created_at AT TIME ZONE %(tz)s)::date AS d, COUNT(*) AS n,
                  COUNT(*) FILTER (WHERE status = 'committed') AS committed,
                  COUNT(*) FILTER (WHERE status = 'error') AS failed
             FROM statements WHERE created_at >= %(a)s AND created_at < %(b)s GROUP BY 1""", b) or []
    per = {k: admin_metrics.per_bucket({r["d"]: int(r[k]) for r in daily}, bkts) for k in ("n", "committed", "failed")}
    by_kind = db.query("""SELECT file_kind AS kind, COUNT(*) AS n, COUNT(*) FILTER (WHERE status = 'error') AS errors
                            FROM statements WHERE created_at >= %(a)s AND created_at < %(b)s GROUP BY 1 ORDER BY n DESC""", b) or []
    # error_message holds the generic user-facing explanations the importer writes, never a file name.
    messages = db.query("""SELECT LEFT(error_message, 160) AS message, COUNT(*) AS n FROM statements
                            WHERE status = 'error' AND created_at >= %(a)s AND created_at < %(b)s
                            GROUP BY 1 ORDER BY n DESC LIMIT 8""", b) or []
    timing = db.query("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY parse_ms) AS p50,
                                percentile_cont(0.95) WITHIN GROUP (ORDER BY parse_ms) AS p95,
                                percentile_cont(0.5) WITHIN GROUP (ORDER BY commit_ms) AS commit_p50
                           FROM statements WHERE created_at >= %(a)s AND created_at < %(b)s""", b, one=True) or {}
    total, failed = sum(per["n"]), sum(per["failed"])
    return {"range": admin_metrics._range_json(rng),
            "totals": {"uploaded": total, "committed": sum(per["committed"]), "failed": failed,
                       "failure_rate": admin_metrics.ratio(failed, total),
                       "p50_ms": timing.get("p50"), "p95_ms": timing.get("p95"), "commit_p50_ms": timing.get("commit_p50")},
            "series": {"labels": [s.isoformat() for s, _e in bkts], **per},
            "by_profile": rows_json(db.query(IMPORTS_BY_PROFILE, b) or []), "by_kind": rows_json(by_kind),
            "error_messages": rows_json(messages)}


def ai_report(rng):
    b = admin_metrics._bounds(rng)
    bkts = admin_range.buckets(rng["start"], rng["end"], rng["bucket"])
    tot = db.query(
        """SELECT COUNT(*) AS calls, COUNT(*) FILTER (WHERE status = 'error') AS errors,
                  COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) AS tokens,
                  COALESCE(SUM(cost_usd), 0) AS cost_usd, COUNT(*) FILTER (WHERE cost_usd IS NULL) AS uncosted,
                  COALESCE(SUM(cost_usd) FILTER (WHERE key_source = 'shared'), 0) AS shared_cost_usd,
                  COUNT(DISTINCT user_id) AS users, AVG(duration_ms) AS avg_ms
             FROM ai_calls WHERE created_at >= %(a)s AND created_at < %(b)s""", b, one=True) or {}
    group = """SELECT {col} AS key, COUNT(*) AS calls, COUNT(*) FILTER (WHERE status = 'error') AS errors,
                      COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) AS tokens,
                      COALESCE(SUM(cost_usd), 0) AS cost_usd
                 FROM ai_calls WHERE created_at >= %(a)s AND created_at < %(b)s GROUP BY 1 ORDER BY calls DESC LIMIT 10"""
    daily = db.query("""SELECT (created_at AT TIME ZONE %(tz)s)::date AS d, COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS cost
                          FROM ai_calls WHERE created_at >= %(a)s AND created_at < %(b)s GROUP BY 1""", b) or []
    calls = int(tot.get("calls") or 0)
    return {"range": admin_metrics._range_json(rng),
            "totals": {"calls": calls, "errors": int(tot.get("errors") or 0),
                       "error_rate": admin_metrics.ratio(int(tot.get("errors") or 0), calls),
                       "tokens": int(tot.get("tokens") or 0), "cost_usd": float(tot.get("cost_usd") or 0),
                       "shared_cost_usd": float(tot.get("shared_cost_usd") or 0),
                       "cost_is_partial": bool(tot.get("uncosted")), "users": int(tot.get("users") or 0),
                       "avg_ms": None if tot.get("avg_ms") is None else int(tot["avg_ms"])},
            "by_purpose": rows_json(db.query(group.format(col="purpose"), b) or []),
            "by_key_source": rows_json(db.query(group.format(col="COALESCE(key_source, 'unknown')"), b) or []),
            "by_model": rows_json(db.query(group.format(col="NULLIF(model, '')"), b) or []),
            "series": {"labels": [s.isoformat() for s, _e in bkts],
                       "calls": admin_metrics.per_bucket({r["d"]: int(r["n"]) for r in daily}, bkts),
                       "cost_usd": [round(v, 4) for v in admin_metrics.per_bucket({r["d"]: float(r["cost"]) for r in daily}, bkts)]}}


@bp.get("/imports")
@admin_required
def imports_route():
    rng, err = admin_metrics._range_or_400()
    if err:
        return err
    return jsonify(admin_metrics.cached(f"imports:{admin_range.cache_key(rng)}", lambda: imports_report(rng),
                                        refresh=request.args.get("refresh") == "1"))


@bp.get("/ai")
@admin_required
def ai_route():
    rng, err = admin_metrics._range_or_400()
    if err:
        return err
    return jsonify(admin_metrics.cached(f"ai:{admin_range.cache_key(rng)}", lambda: ai_report(rng),
                                        refresh=request.args.get("refresh") == "1"))

