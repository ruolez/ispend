"""Admin console: user lifecycle, instance statistics and the activity log.

Every view here is @admin_required. The user endpoints moved out of settings_api because
DELETE changed meaning (it used to deactivate, it now soft-deletes and hides the user) --
moving the path makes the old URL 404 loudly instead of silently doing something different.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from decimal import Decimal

from flask import Blueprint, jsonify, request, session
from werkzeug.security import generate_password_hash

import config
import db
import seed_categories
import user_state
from auth import MIN_PASSWORD_LEN, admin_required, password_problem
from util import api_error, audit, audit_retention_days, csv_response, iso, json_body, rows_json, to_int

bp = Blueprint("admin", __name__, url_prefix="/api/admin")

# Sort keys are interpolated into SQL, so they may only ever come from this dict.
USER_SORTS = {
    "id": "u.id", "username": "u.username", "created_at": "u.created_at",
    "last_login_at": "u.last_login_at", "txn_count": "txn_count", "storage_bytes": "storage_bytes",
}
DEFAULT_RETENTION_DAYS = 30
STATS_TTL_SECONDS = 300


def _uid():
    return session["user_id"]


def _not_self(user_id, verb):
    if user_id == _uid():
        return api_error(f"You cannot {verb} your own account")
    return None


def _load(user_id):
    return db.query(
        "SELECT id, username, role, status, locked_at, lock_reason, deleted_at FROM users WHERE id = %s",
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


# ---------- Users ----------

_LIST_USERS_SQL = """
SELECT u.id, u.username, u.email, u.email_verified_at, u.role, u.status, u.locked_at,
       u.lock_reason, u.deleted_at, u.created_at, u.last_login_at,
       COALESCE(t.n, 0) AS txn_count,
       COALESCE(a.n, 0) AS account_count,
       COALESCE(s.n, 0) AS statement_count,
       COALESCE(s.bytes, 0) AS storage_bytes,
       (u.deleted_at IS NOT NULL AND %(retention)s > 0
        AND u.deleted_at < now() - make_interval(days => %(retention)s)) AS purge_due
  FROM users u
  LEFT JOIN (SELECT user_id, COUNT(*) n FROM transactions GROUP BY user_id) t ON t.user_id = u.id
  LEFT JOIN (SELECT user_id, COUNT(*) n FROM accounts GROUP BY user_id) a ON a.user_id = u.id
  LEFT JOIN (SELECT user_id, COUNT(*) n, SUM(file_size) bytes
               FROM (SELECT DISTINCT ON (user_id, file_sha256) user_id, file_size
                       FROM statements WHERE status <> 'discarded'
                      ORDER BY user_id, file_sha256, id) d
              GROUP BY user_id) s ON s.user_id = u.id
 WHERE u.status = ANY(%(statuses)s)
   AND (%(role)s IS NULL OR u.role = %(role)s)
   AND (%(q)s IS NULL OR u.username ILIKE %(q)s OR u.email ILIKE %(q)s)
 ORDER BY {order}, u.id
 LIMIT %(limit)s
"""


@bp.get("/users")
@admin_required
def list_users():
    status = request.args.get("status") or "default"
    if status == "all":
        statuses = list(user_state.STATUSES)
    elif status in user_state.STATUSES:
        statuses = [status]
    else:
        statuses = [user_state.ACTIVE, user_state.LOCKED]
    role = request.args.get("role")
    if role not in ("admin", "user"):
        role = None
    q = (request.args.get("q") or "").strip()
    sort = USER_SORTS.get(request.args.get("sort"), "u.id")
    direction = "DESC" if request.args.get("dir") == "desc" else "ASC"
    nulls = " NULLS LAST" if direction == "DESC" else ""
    retention = _retention_days()
    rows = db.query(
        _LIST_USERS_SQL.format(order=f"{sort} {direction}{nulls}"),
        {"statuses": statuses, "role": role, "q": f"%{q}%" if q else None,
         "retention": retention, "limit": to_int(request.args.get("limit"), "limit", lo=1, hi=1000) or 200},
    ) or []
    me = _uid()
    items = [{**r, "is_self": r["id"] == me} for r in rows_json(rows)]
    # Billing is optional; the users list must render on an install that has none.
    try:
        import admin_billing
        blocks = admin_billing.users_block([r["id"] for r in items])
        for item in items:
            item["billing"] = blocks.get(item["id"])
    except Exception:
        for item in items:
            item["billing"] = None
    due = db.query(
        """SELECT COUNT(*) AS n FROM users
            WHERE status = 'deleted' AND %(retention)s > 0
              AND deleted_at < now() - make_interval(days => %(retention)s)""",
        {"retention": retention}, one=True) or {"n": 0}
    return jsonify({"items": items, "retention_days": retention, "purge_due_count": due["n"]})


@bp.post("/users")
@admin_required
def create_user():
    data = json_body()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = data.get("role") if data.get("role") in ("admin", "user") else "user"
    if not username or password_problem(password):
        return api_error(f"Username and a password of at least {MIN_PASSWORD_LEN} characters are required")
    existing = db.query("SELECT id, status, username FROM users WHERE username = %s", (username,), one=True)
    if existing and existing["status"] == user_state.DELETED:
        # A deleted user keeps their username reserved (renaming would break restore and poison
        # the audit trail), so turn the dead end into the action the admin actually wants.
        return jsonify({"error": f"“{username}” was deleted and can be restored.",
                        "code": "username_deleted", "user_id": existing["id"]}), 409
    if existing:
        return api_error("Username already exists")
    row = db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING id",
        (username, generate_password_hash(password), role),
        returning=True,
    )
    seed_categories.seed_for_user(db.get_db(), row["id"])
    audit("user.create", {"username": username, "role": role})
    return jsonify({"id": row["id"]}), 201


@bp.put("/users/<int:user_id>")
@admin_required
def update_user(user_id):
    data = json_body()
    if not _load(user_id):
        return api_error("User not found", 404)
    if "role" in data:
        if data["role"] not in ("admin", "user"):
            return api_error("Invalid role")
        if data["role"] != "admin":
            if user_id == _uid():
                return api_error("You cannot remove your own admin role")
            n = db.execute(
                "UPDATE users SET role = 'user', updated_at = now() WHERE id = %(id)s" + _LAST_ADMIN_GUARD,
                {"id": user_id, "uid": user_id})
            if not n:
                return api_error(LAST_ADMIN_ERROR, 409)
        else:
            db.execute("UPDATE users SET role = 'admin', updated_at = now() WHERE id = %s", (user_id,))
    audit("user.update", {"id": user_id, "fields": [k for k in data if k == "role"]})
    return jsonify({"ok": True})


@bp.put("/users/<int:user_id>/password")
@admin_required
def reset_password(user_id):
    data = json_body()
    password = data.get("password") or ""
    problem = password_problem(password)
    if problem:
        return api_error(problem)
    if not _load(user_id):
        return api_error("User not found", 404)
    db.execute("UPDATE users SET password_hash = %s, updated_at = now() WHERE id = %s",
               (generate_password_hash(password), user_id))
    audit("user.password_reset", {"id": user_id})
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
    audit("user.lock", {"id": user_id, "username": row["username"], "reason": reason})
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
    audit("user.unlock", {"id": user_id, "username": row["username"]})
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
    audit("user.restore", {"id": user_id, "username": row["username"], "status": target})
    return jsonify({"ok": True, "status": target})


@bp.delete("/users/<int:user_id>")
@admin_required
def delete_user(user_id):
    blocked = _not_self(user_id, "delete")
    if blocked:
        return blocked
    row = _load(user_id)
    if not row:
        return api_error("User not found", 404)
    if request.args.get("permanent") == "true":
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
    audit("user.delete", {"id": user_id, "username": row["username"]})
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

BUCKETS = {"day": "day", "week": "week", "month": "month"}


def _bucket_for(days):
    return "day" if days <= 90 else ("week" if days <= 365 else "month")


@bp.get("/stats/overview")
@admin_required
def stats_overview():
    days = to_int(request.args.get("days"), "days", lo=1, hi=3650) or 90
    if days not in (7, 30, 90, 365):
        days = 90
    cache_key = f"admin:stats:overview:{days}"
    if request.args.get("refresh") != "1":
        cached = db.get_setting(cache_key)
        if cached:
            try:
                payload = json.loads(cached)
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(payload["generated_at"])).total_seconds()
                if age < STATS_TTL_SECONDS:
                    return jsonify(payload)
            except (ValueError, KeyError, TypeError):
                pass
    payload = _build_overview(days)
    db.set_setting(cache_key, json.dumps(payload, default=str))
    return jsonify(payload)


def _build_overview(days):
    bucket = _bucket_for(days)
    users = db.query("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status='active')  AS active,
               COUNT(*) FILTER (WHERE status='locked')  AS locked,
               COUNT(*) FILTER (WHERE status='deleted') AS deleted,
               COUNT(*) FILTER (WHERE role='admin' AND status='active') AS admins,
               COUNT(*) FILTER (WHERE status<>'deleted' AND last_login_at >= now() - interval '7 days')  AS active_7d,
               COUNT(*) FILTER (WHERE status<>'deleted' AND last_login_at >= now() - interval '30 days') AS active_30d,
               COUNT(*) FILTER (WHERE status<>'deleted' AND last_login_at IS NULL) AS never_signed_in,
               COUNT(*) FILTER (WHERE created_at >= now() - interval '30 days') AS new_30d
          FROM users""", one=True) or {}
    # "Signed in recently" and "actually did something recently" answer different questions and
    # are both shown, labelled, because a user can log in and do nothing.
    by_activity = db.query("""
        SELECT COUNT(DISTINCT user_id) AS n FROM (
          SELECT user_id FROM statements WHERE created_at >= now() - interval '30 days'
          UNION
          SELECT user_id FROM transaction_events
           WHERE created_at >= now() - interval '30 days' AND user_id IS NOT NULL) s""", one=True) or {"n": 0}

    totals = db.query("""
        SELECT (SELECT COUNT(*) FROM transactions) AS transactions,
               (SELECT COUNT(*) FROM accounts) AS accounts,
               (SELECT COUNT(*) FROM statements WHERE status='committed') AS statements,
               (SELECT COUNT(*) FROM rules) AS rules,
               (SELECT COUNT(*) FROM budgets) AS budgets,
               (SELECT MIN(txn_date) FROM transactions) AS first_txn_date,
               (SELECT MAX(txn_date) FROM transactions) AS last_txn_date""", one=True) or {}

    # Every series is gap-filled off the same generate_series so both charts share one x-axis;
    # a sparse series would silently hide days that have statements but no transactions.
    def _series(sql_from, cols):
        return db.query(f"""
            SELECT d::date AS t, {cols}
              FROM generate_series(date_trunc('{bucket}', now()) - make_interval(days => %s),
                                   date_trunc('{bucket}', now()), interval '1 {bucket}') d
              LEFT JOIN {sql_from} x ON x.created_at >= d AND x.created_at < d + interval '1 {bucket}'
             GROUP BY d ORDER BY d""", (days,)) or []

    series = {
        "signups": _series("users", "COUNT(x.id) AS n"),
        # Free: audit() already writes auth.login on every login. Bounded by the retention setting.
        "logins": _series("(SELECT id, user_id, created_at FROM audit_log WHERE action = 'auth.login')",
                          "COUNT(x.id) AS logins, COUNT(DISTINCT x.user_id) AS users"),
        "transactions": _series("transactions", "COUNT(x.id) AS n"),
        "statements": _series("statements", "COUNT(x.id) AS n, COUNT(*) FILTER (WHERE x.status='committed') AS committed"),
    }

    src = db.query(_SOURCE_BYTES_SQL.format(where=""), one=True) or {}
    source_bytes = int(src.get("source_bytes") or 0)
    disk_bytes, disk_ok = _disk_usage()
    storage = {
        "disk_bytes": disk_bytes,
        "source_bytes": source_bytes,
        "derived_bytes": (disk_bytes - source_bytes) if disk_ok and disk_bytes is not None else None,
        "unique_files": src.get("unique_files") or 0,
        "disk_scan_ok": disk_ok,
    }

    ai = db.query("""
        SELECT COUNT(*) AS calls, COUNT(*) FILTER (WHERE status='error') AS errors,
               COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens),0) AS completion_tokens,
               COUNT(DISTINCT user_id) AS users, ROUND(AVG(duration_ms)) AS avg_ms
          FROM ai_calls WHERE created_at >= now() - make_interval(days => %s)""", (days,), one=True) or {}
    ai = dict(ai)
    ai["error_rate"] = round((ai.get("errors") or 0) / ai["calls"], 4) if ai.get("calls") else 0
    # Tokens, never dollars: prices live only in the OpenRouter catalog, drift, and the call can
    # fail -- a server-side cost figure would be confidently wrong.
    ai["by_model"] = db.query("""
        SELECT model, COUNT(*) AS calls,
               COALESCE(SUM(prompt_tokens),0) + COALESCE(SUM(completion_tokens),0) AS tokens,
               COUNT(*) FILTER (WHERE status='error') AS errors
          FROM ai_calls WHERE created_at >= now() - make_interval(days => %s)
         GROUP BY model ORDER BY calls DESC LIMIT 8""", (days,)) or []

    imports = {
        "by_profile": db.query("""
            SELECT COALESCE(bank_profile,'unknown') AS profile, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE ocr_applied) AS ocr,
                   COUNT(*) FILTER (WHERE status='error') AS errors
              FROM statements GROUP BY 1 ORDER BY n DESC LIMIT 12""") or [],
        "by_kind": db.query("SELECT file_kind AS kind, COUNT(*) AS n FROM statements GROUP BY 1 ORDER BY n DESC") or [],
    }

    house = db.query("""
        SELECT (SELECT COUNT(*) FROM audit_log) AS audit_rows,
               (SELECT MIN(created_at) FROM audit_log) AS oldest_audit_at""", one=True) or {}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days, "bucket": bucket,
        "users": {**dict(users), "active_by_activity_30d": by_activity["n"]},
        "totals": dict(rows_json([totals])[0]) if totals else {},
        "series": {k: rows_json(v) for k, v in series.items()},
        "storage": storage,
        "ai": {k: (float(v) if isinstance(v, Decimal) else v) for k, v in ai.items() if k != "by_model"}
              | {"by_model": rows_json(ai["by_model"])},
        "imports": {k: rows_json(v) for k, v in imports.items()},
        "housekeeping": {
            "audit_retention_days": audit_retention_days(),
            "deleted_user_retention_days": _retention_days(),
            "audit_rows": house.get("audit_rows", 0),
            "oldest_audit_at": iso(house.get("oldest_audit_at")),
        },
    }


@bp.get("/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    row = db.query(
        """SELECT id, username, role, status, locked_at, lock_reason, deleted_at, created_at,
                  last_login_at, preferences
             FROM users WHERE id = %s""", (user_id,), one=True)
    if not row:
        return api_error("User not found", 404)
    counts = db.query("""
        SELECT (SELECT COUNT(*) FROM accounts WHERE user_id = %(u)s) AS accounts,
               (SELECT COUNT(*) FROM transactions WHERE user_id = %(u)s) AS transactions,
               (SELECT COUNT(*) FROM statements WHERE user_id = %(u)s AND status='committed') AS statements,
               (SELECT COUNT(*) FROM categories WHERE user_id = %(u)s) AS categories,
               (SELECT COUNT(*) FROM rules WHERE user_id = %(u)s) AS rules,
               (SELECT COUNT(*) FROM budgets WHERE user_id = %(u)s) AS budgets,
               (SELECT COUNT(*) FROM tags WHERE user_id = %(u)s) AS tags,
               (SELECT COUNT(*) FROM merchant_memory WHERE user_id = %(u)s) AS merchants,
               (SELECT COUNT(*) FROM insights WHERE user_id = %(u)s) AS insights,
               -- Per-user settings live in the GLOBAL settings table under a 'u<id>:' prefix, so a
               -- "tables with user_id" sweep misses them. This is the only place they are visible.
               (SELECT COUNT(*) FROM settings WHERE key LIKE %(pfx)s) AS settings_rows,
               (SELECT MIN(txn_date) FROM transactions WHERE user_id = %(u)s) AS first_txn,
               (SELECT MAX(txn_date) FROM transactions WHERE user_id = %(u)s) AS last_txn,
               (SELECT MAX(created_at) FROM statements WHERE user_id = %(u)s) AS last_import_at
        """, {"u": user_id, "pfx": f"u{user_id}:%"}, one=True) or {}
    counts = dict(rows_json([counts])[0]) if counts else {}
    src = db.query(_SOURCE_BYTES_SQL.format(where="AND user_id = %(u)s"), {"u": user_id}, one=True) or {}
    disk_bytes, disk_ok = _disk_usage(user_id)
    ai = db.query("""
        SELECT COUNT(*) AS calls, COUNT(*) FILTER (WHERE status='error') AS errors,
               COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens),0) AS completion_tokens,
               MAX(created_at) AS last_call_at
          FROM ai_calls WHERE user_id = %s""", (user_id,), one=True) or {}
    mix = db.query("""
        SELECT COALESCE(category_source,'none') AS source, category_status, COUNT(*) AS n
          FROM transactions WHERE user_id = %s GROUP BY 1,2 ORDER BY n DESC""", (user_id,)) or []
    activity = db.query(
        "SELECT id, action, detail, created_at FROM audit_log WHERE user_id = %s ORDER BY id DESC LIMIT 20",
        (user_id,)) or []
    user = dict(rows_json([row])[0])
    prefs = user.pop("preferences", None) or {}
    user["preferences_keys"] = sorted(prefs)
    return jsonify({
        "user": user,
        "counts": {k: v for k, v in counts.items() if k not in ("first_txn", "last_txn", "last_import_at")},
        "data_range": {"first_txn": counts.get("first_txn"), "last_txn": counts.get("last_txn"),
                       "last_import_at": counts.get("last_import_at")},
        "storage": {"disk_bytes": disk_bytes, "source_bytes": int(src.get("source_bytes") or 0),
                    "unique_files": src.get("unique_files", 0), "disk_scan_ok": disk_ok},
        "categorization": rows_json(mix),
        "ai": rows_json([ai])[0] if ai else {},
        "recent_activity": rows_json(activity),
    })


# ---------- Activity log ----------

_AUDIT_SQL = """
SELECT a.id, a.user_id, u.username, a.action, a.detail, a.created_at
  FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
 WHERE (%(cursor)s IS NULL OR a.id < %(cursor)s)
   AND (%(user_id)s IS NULL OR a.user_id = %(user_id)s)
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
    q = (request.args.get("q") or "").strip()
    limit = to_int(request.args.get("limit"), "limit", lo=1, hi=500) or 50
    params = {
        "cursor": to_int(request.args.get("cursor"), "cursor"),
        "user_id": to_int(request.args.get("user_id"), "user_id"),
        "action": request.args.get("action") or None,
        "from": request.args.get("from") or None,
        "to": request.args.get("to") or None,
        "q": f"%{q}%" if q else None,
        "limit": 10000 if request.args.get("format") == "csv" else limit,
    }
    rows = rows_json(db.query(_AUDIT_SQL, params) or [])
    if request.args.get("format") == "csv":
        flat = [{**r, "detail": json.dumps(r["detail"]) if r["detail"] is not None else ""} for r in rows]
        return csv_response(flat, "activity.csv", ["created_at", "id", "user_id", "username", "action", "detail"])
    return jsonify({"items": rows, "next_cursor": rows[-1]["id"] if len(rows) == limit else None})


@bp.get("/audit/actions")
@admin_required
def audit_actions():
    return jsonify(rows_json(db.query(
        "SELECT action, COUNT(*) AS n FROM audit_log GROUP BY action ORDER BY n DESC") or []))


# ---------- Housekeeping settings ----------

ADMIN_SETTINGS = {
    "audit_retention_days": (7, 3650),
    "admin_deleted_user_retention_days": (0, 3650),
}


@bp.get("/settings")
@admin_required
def get_admin_settings():
    return jsonify({"audit_retention_days": audit_retention_days(),
                    "deleted_user_retention_days": _retention_days()})


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
