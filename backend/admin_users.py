"""Admin › Users: the people list (filters, paging, export, bulk actions), the one-person view, and
the actions and bookkeeping (notes, tags) an operator needs.

Privacy rule for everything here: counts, dates and states — never anyone's transactions,
merchants, balances, file names or amounts they entered. The 360 view's payload is pinned by a
test on its exact key set.
"""

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

from flask import Blueprint, jsonify, request, session

import admin_privacy
import auth
import config
import db
import user_state
from auth import admin_required, step_up_missing, step_up_required
from util import api_error, audit, csv_response, iso, json_body, rows_json, to_int

bp = Blueprint("admin_users", __name__, url_prefix="/api/admin")

PER_PAGE = 50
MAX_PER_PAGE = 100
MAX_EXPORT = 10000
MAX_BULK = 500
DEFAULT_RETENTION_DAYS = 30
STATES = ("trialing", "active", "grace", "read_only", "admin_exempt")
TAG_COLORS = tuple(f"c{i}" for i in range(1, 13))

# Every accepted list parameter; the admin's saved views may only hold these.
FILTER_KEYS = ("q", "status", "role", "state", "plan", "trial_ending", "confirmed", "activated",
               "seen_within", "inactive_for", "signup_from", "signup_to", "source", "tag", "sort", "dir")

TXN_COUNT = "(SELECT COUNT(*) FROM transactions t WHERE t.user_id = u.id)"
STORAGE = ("(SELECT COALESCE(SUM(file_size), 0) FROM (SELECT DISTINCT ON (file_sha256) file_size FROM statements"
           " WHERE user_id = u.id AND status <> 'discarded' ORDER BY file_sha256, id) d)")
LIFETIME = ("(SELECT COALESCE(SUM(amount_paid_cents - amount_refunded_cents), 0) FROM payments p"
            " WHERE p.user_id = u.id)")
ACTIVATED = "(u.first_commit_at IS NOT NULL AND u.first_commit_at < u.created_at + interval '7 days')"

# Sort keys are interpolated into SQL, so they may only ever come from this dict.
SORTS = {
    "id": "u.id", "username": "lower(u.username)", "email": "lower(u.email)", "role": "u.role",
    "status": "u.status", "created_at": "u.created_at", "last_login_at": "u.last_login_at",
    "last_seen_at": "u.last_seen_at", "state": "s.ent_state", "plan": "s.plan", "mrr": "s.mrr_cents",
    "txn_count": TXN_COUNT, "storage_bytes": STORAGE, "lifetime": LIFETIME,
}


def _retention_days():
    raw = db.get_setting("admin_deleted_user_retention_days")
    try:
        return max(0, min(3650, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def _local_day_start(value, name):
    """A YYYY-MM-DD from the browser as the start of that local day."""
    from zoneinfo import ZoneInfo
    try:
        d = datetime.strptime(str(value), "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{name} must be a date like 2026-01-31") from None
    return d.replace(tzinfo=ZoneInfo(config.APP_TIMEZONE))


def user_filters(args):
    """(where clauses, params) for the people list. args is any mapping of list parameters (the
    request's, or a saved query string's for bulk actions). Unknown values are ignored rather than
    rejected, so a stale bookmark still opens."""
    where, p = [], {}
    status = args.get("status") or ""
    if status == "all":
        statuses = list(user_state.STATUSES)
    elif status in user_state.STATUSES:
        statuses = [status]
    else:
        statuses = [user_state.ACTIVE, user_state.LOCKED]
    where.append("u.status = ANY(%(statuses)s)")
    p["statuses"] = statuses
    if args.get("role") in ("admin", "user"):
        where.append("u.role = %(role)s")
        p["role"] = args["role"]
    q = (args.get("q") or "").strip()
    if q:
        digits = q.lstrip("#")
        if digits.isdigit():
            where.append("(u.id = %(qid)s OR u.username ILIKE %(q)s OR u.email ILIKE %(q)s)")
            p["qid"] = int(digits)
        else:
            where.append("(u.username ILIKE %(q)s OR u.email ILIKE %(q)s)")
        p["q"] = f"%{q}%"
    states = [s for s in (args.get("state") or "").split(",") if s]
    ent = [s for s in states if s in STATES]
    if ent or "comped" in states:
        parts = []
        if ent:
            parts.append("s.ent_state = ANY(%(states)s)")
            p["states"] = ent
        if "comped" in states:
            parts.append("(s.comped_until IS NOT NULL AND s.comped_until > now())")
        where.append("(" + " OR ".join(parts) + ")")
    if args.get("plan") in ("monthly", "yearly"):
        where.append("s.plan = %(plan)s AND s.mrr_cents > 0")
        p["plan"] = args["plan"]
    days = _int(args.get("trial_ending"), 1, 365)
    if days:
        where.append("s.ent_state = 'trialing' AND s.trial_end < now() + make_interval(days => %(trial_days)s)")
        p["trial_days"] = days
    if args.get("confirmed") in ("1", "0"):
        where.append("u.email_verified_at IS " + ("NOT NULL" if args["confirmed"] == "1" else "NULL"))
    if args.get("activated") in ("1", "0"):
        where.append(ACTIVATED if args["activated"] == "1" else f"NOT {ACTIVATED}")
    days = _int(args.get("seen_within"), 1, 3650)
    if days:
        where.append("u.last_seen_at > now() - make_interval(days => %(seen_within)s)")
        p["seen_within"] = days
    days = _int(args.get("inactive_for"), 1, 3650)
    if days:
        where.append("(u.last_seen_at IS NULL OR u.last_seen_at < now() - make_interval(days => %(inactive_for)s))")
        p["inactive_for"] = days
    if args.get("signup_from"):
        where.append("u.created_at >= %(signup_from)s")
        p["signup_from"] = _local_day_start(args["signup_from"], "signup_from")
    if args.get("signup_to"):
        where.append("u.created_at < %(signup_to)s")
        p["signup_to"] = _local_day_start(args["signup_to"], "signup_to") + timedelta(days=1)
    source = (args.get("source") or "").strip().lower()
    if source == "unknown":
        where.append("sa.user_id IS NULL")
    elif source:
        where.append("sa.channel = %(source)s")
        p["source"] = source[:100]
    tags = [int(t) for t in (args.get("tag") or "").split(",") if t.strip().isdigit()]
    if tags:
        where.append("EXISTS (SELECT 1 FROM user_admin_tags ut WHERE ut.user_id = u.id AND ut.tag_id = ANY(%(tags)s))")
        p["tags"] = tags
    return where, p


def _int(value, lo, hi):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def _order(args):
    sort = SORTS.get(args.get("sort"), "u.id")
    direction = "DESC" if args.get("dir") == "desc" else "ASC"
    return f"{sort} {direction}{' NULLS LAST' if direction == 'DESC' else ' NULLS FIRST'}"


FROM = """FROM users u
  LEFT JOIN subscriptions s ON s.user_id = u.id
  LEFT JOIN signup_attribution sa ON sa.user_id = u.id"""


def matching_ids(args, limit, offset=0):
    """([ids], total) for one page of the filtered, sorted list."""
    where, params = user_filters(args)
    rows = db.query(
        f"""SELECT u.id, COUNT(*) OVER () AS total {FROM}
             WHERE {' AND '.join(where)}
             ORDER BY {_order(args)}, u.id
             LIMIT %(limit)s OFFSET %(offset)s""",
        {**params, "limit": limit, "offset": offset}) or []
    return [r["id"] for r in rows], (rows[0]["total"] if rows else 0)


ROWS_SQL = f"""
SELECT u.id, u.username, u.email, u.email_verified_at, u.verification_required, u.role, u.status,
       u.locked_at, u.lock_reason, u.deleted_at, u.created_at, u.last_login_at, u.last_seen_at,
       u.last_active_at, u.first_commit_at,
       s.ent_state AS state, s.plan, s.mrr_cents, s.currency, s.trial_end, s.comped_until,
       sa.channel AS source, {ACTIVATED} AS activated,
       {TXN_COUNT} AS txn_count,
       (SELECT COUNT(*) FROM statements st WHERE st.user_id = u.id AND st.status = 'committed') AS statement_count,
       {STORAGE} AS storage_bytes,
       {LIFETIME} AS lifetime_cents,
       (u.deleted_at IS NOT NULL AND %(retention)s > 0
        AND u.deleted_at < now() - make_interval(days => %(retention)s)) AS purge_due,
       COALESCE((SELECT json_agg(json_build_object('id', t.id, 'name', t.name, 'color', t.color) ORDER BY lower(t.name))
                   FROM user_admin_tags ut JOIN admin_tags t ON t.id = ut.tag_id WHERE ut.user_id = u.id),
                '[]'::json) AS tags
  {FROM}
 WHERE u.id = ANY(%(ids)s)
"""


def user_rows(ids):
    """The list rows for these ids, in this order."""
    if not ids:
        return []
    rows = {r["id"]: r for r in rows_json(db.query(ROWS_SQL, {"ids": list(ids), "retention": _retention_days()}) or [])}
    me = session.get("user_id")
    out = []
    for uid in ids:
        r = rows.get(uid)
        if r:
            r["is_self"] = uid == me
            r["email_confirmed"] = bool(r.pop("email_verified_at", None))
            out.append(r)
    return out


@bp.get("/users")
@admin_required
def list_users():
    args = request.args
    if args.get("format") == "csv":
        return step_up_missing() or _export(args)
    per_page = to_int(args.get("per_page"), "per_page", lo=1, hi=MAX_PER_PAGE) or PER_PAGE
    page = to_int(args.get("page"), "page", lo=1, hi=100000) or 1
    try:
        ids, total = matching_ids(args, per_page, (page - 1) * per_page)
    except ValueError as e:
        return api_error(str(e))
    items = user_rows(ids)
    try:
        import admin_billing
        blocks = admin_billing.users_block(ids)
        for item in items:
            item["billing"] = blocks.get(item["id"])
    except Exception:
        for item in items:
            item["billing"] = None
    retention = _retention_days()
    due = db.query(
        """SELECT COUNT(*) AS n FROM users
            WHERE status = 'deleted' AND %(retention)s > 0
              AND deleted_at < now() - make_interval(days => %(retention)s)""",
        {"retention": retention}, one=True) or {"n": 0}
    return jsonify({"items": items, "total": total, "page": page, "per_page": per_page,
                    "retention_days": retention, "purge_due_count": due["n"]})


EXPORT_COLUMNS = ["id", "username", "email", "email_confirmed", "role", "status", "state", "plan", "mrr_cents",
                  "currency", "trial_end", "created_at", "last_seen_at", "activated", "source", "tags",
                  "txn_count", "statement_count", "lifetime_cents"]


def _export(args):
    try:
        ids, total = matching_ids(args, MAX_EXPORT)
    except ValueError as e:
        return api_error(str(e))
    rows = user_rows(ids)
    for r in rows:
        r["tags"] = ", ".join(t["name"] for t in r.get("tags") or [])
    audit("admin.users.export", {"count": len(rows), "filtered": bool(set(args) - {"format", "sort", "dir"})})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return csv_response(rows, f"ispend-users-{stamp}.csv", EXPORT_COLUMNS)


@bp.get("/users/facets")
@admin_required
def facets():
    """What the list's filter menus offer: every sign-up source and tag, with how many people."""
    sources = db.query(
        """SELECT COALESCE(sa.channel, 'unknown') AS channel, COUNT(*) AS n
             FROM users u LEFT JOIN signup_attribution sa ON sa.user_id = u.id
            WHERE u.status <> 'deleted' GROUP BY 1 ORDER BY n DESC, 1 LIMIT 30""") or []
    tags = db.query(
        """SELECT t.id, t.name, t.color, COUNT(ut.user_id) AS n
             FROM admin_tags t LEFT JOIN user_admin_tags ut ON ut.tag_id = t.id
            GROUP BY t.id ORDER BY lower(t.name)""") or []
    return jsonify({"sources": rows_json(sources), "tags": rows_json(tags)})


# ---------- bulk ----------

BULK_ACTIONS = ("lock", "unlock", "tag", "untag", "extend_trial")


def _bulk_targets(data):
    """Explicit ids, or every match of a list query string (the admin's current filters)."""
    if data.get("ids") is not None:
        ids = [int(i) for i in data["ids"] if str(i).isdigit()]
        return list(dict.fromkeys(ids))
    query = data.get("query")
    if isinstance(query, str):
        args = {k: v[0] for k, v in parse_qs(query.lstrip("?")).items() if k in FILTER_KEYS}
        ids, _total = matching_ids(args, MAX_BULK + 1)
        return ids
    return None


@bp.post("/users/bulk")
@admin_required
@step_up_required
def bulk():
    data = json_body()
    action = data.get("action")
    if action not in BULK_ACTIONS:
        return api_error(f"action must be one of {', '.join(BULK_ACTIONS)}")
    ids = _bulk_targets(data)
    if ids is None:
        return api_error("Send ids or the list query to act on")
    if not ids:
        return api_error("Nobody matches")
    if len(ids) > MAX_BULK:
        return api_error(f"At most {MAX_BULK} people at once; narrow the filters", 413)
    params = data.get("params") or {}
    tag_ids = [int(t) for t in (params.get("tag_ids") or []) if str(t).isdigit()]
    days = _int(params.get("days"), 1, 365)
    if action in ("tag", "untag") and not tag_ids:
        return api_error("Choose at least one tag")
    if action == "extend_trial" and not days:
        return api_error("days must be between 1 and 365")
    me = session["user_id"]
    done, skipped = 0, []
    reason = (params.get("reason") or "").strip()[:200] or None
    for uid in ids:
        if uid == me and action in ("lock",):
            skipped.append({"id": uid, "reason": "yourself"})
            continue
        ok, why = _bulk_one(action, uid, tag_ids, days, reason, me)
        if ok:
            done += 1
        else:
            skipped.append({"id": uid, "reason": why})
    audit("admin.users.bulk", {"action": action, "affected": done, "skipped": len(skipped)})
    return jsonify({"affected": done, "skipped": skipped})


def _bulk_one(action, uid, tag_ids, days, reason, me):
    import admin_api
    row = db.query("SELECT id, username, role, status FROM users WHERE id = %s", (uid,), one=True)
    if not row:
        return False, "not found"
    if action == "lock":
        if row["status"] != user_state.ACTIVE:
            return False, row["status"]
        n = db.execute(
            """UPDATE users SET status = 'locked', locked_at = now(), lock_reason = %(reason)s, updated_at = now()
                WHERE id = %(id)s""" + admin_api._LAST_ADMIN_GUARD,
            {"id": uid, "uid": uid, "reason": reason})
        if not n:
            return False, "last administrator"
        audit("user.lock", {"id": uid, "username": row["username"], "reason": reason, "bulk": True}, target=uid)
    elif action == "unlock":
        if row["status"] != user_state.LOCKED:
            return False, row["status"]
        db.execute("""UPDATE users SET status = 'active', locked_at = NULL, lock_reason = NULL, updated_at = now()
                       WHERE id = %s""", (uid,))
        audit("user.unlock", {"id": uid, "username": row["username"], "bulk": True}, target=uid)
    elif action == "tag":
        for tid in tag_ids:
            db.execute("""INSERT INTO user_admin_tags (user_id, tag_id, created_by) VALUES (%s, %s, %s)
                          ON CONFLICT DO NOTHING""", (uid, tid, me))
    elif action == "untag":
        db.execute("DELETE FROM user_admin_tags WHERE user_id = %s AND tag_id = ANY(%s)", (uid, tag_ids))
    elif action == "extend_trial":
        if row["role"] == "admin":
            return False, "administrator"
        _extend_trial(uid, days)
        audit("billing.extend_trial", {"id": uid, "days": days, "bulk": True}, target=uid)
    return True, None


def _extend_trial(uid, days):
    import ledger
    db.execute("INSERT INTO subscriptions (user_id, status) VALUES (%s, 'none') ON CONFLICT (user_id) DO NOTHING",
               (uid,))
    db.execute(
        """UPDATE subscriptions
              SET trial_end = GREATEST(COALESCE(trial_end, now()), now()) + make_interval(days => %s),
                  status = CASE WHEN status IN ('none','trialing','comped') THEN 'trialing' ELSE status END,
                  lapsed_at = NULL, grace_until = NULL,
                  trial_ending_email_at = NULL, trial_ended_email_at = NULL, updated_at = now()
            WHERE user_id = %s""", (days, uid))
    ledger.record_admin(uid, "trial_extended", {"days": days})
    ledger.refresh_ent_state(uid)


# ---------- one person ----------

def _load(uid):
    return db.query(
        """SELECT id, username, email, email_verified_at, verification_required, role, status, locked_at,
                  lock_reason, deleted_at, created_at, last_login_at, last_seen_at, last_active_at,
                  first_upload_at, first_commit_at, preferences
             FROM users WHERE id = %s""", (uid,), one=True)


@bp.get("/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    import admin_api
    row = _load(user_id)
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
    src = db.query(admin_api._SOURCE_BYTES_SQL.format(where="AND user_id = %(u)s"), {"u": user_id}, one=True) or {}
    disk_bytes, disk_ok = admin_api._disk_usage(user_id)
    ai = db.query("""
        SELECT COUNT(*) AS calls, COUNT(*) FILTER (WHERE status='error') AS errors,
               COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
               COALESCE(SUM(completion_tokens),0) AS completion_tokens,
               COALESCE(SUM(cost_usd) FILTER (WHERE key_source = 'shared'), 0) AS shared_cost_usd,
               MAX(created_at) AS last_call_at
          FROM ai_calls WHERE user_id = %s""", (user_id,), one=True) or {}
    mix = db.query("""
        SELECT COALESCE(category_source,'none') AS source, category_status, COUNT(*) AS n
          FROM transactions WHERE user_id = %s GROUP BY 1,2 ORDER BY n DESC""", (user_id,)) or []
    activity = db.query(
        """SELECT id, action, detail, by_admin, created_at FROM audit_log
            WHERE user_id = %s ORDER BY id DESC LIMIT 20""", (user_id,)) or []
    user = dict(rows_json([row])[0])
    prefs = user.pop("preferences", None) or {}
    user["preferences_keys"] = sorted(prefs)
    user["email_confirmed"] = bool(user.pop("email_verified_at", None))
    return jsonify({
        "user": user,
        "counts": {k: v for k, v in counts.items() if k not in ("first_txn", "last_txn", "last_import_at")},
        "data_range": {"first_txn": counts.get("first_txn"), "last_txn": counts.get("last_txn"),
                       "last_import_at": counts.get("last_import_at")},
        "storage": {"disk_bytes": disk_bytes, "source_bytes": int(src.get("source_bytes") or 0),
                    "unique_files": src.get("unique_files", 0), "disk_scan_ok": disk_ok},
        "categorization": rows_json(mix),
        "ai": rows_json([ai])[0] if ai else {},
        "recent_activity": [admin_privacy.redact_row(r) for r in rows_json(activity)],
        **person_extras(row),
    })


def person_extras(row):
    """Access, money, activation, sign-ins, notes, tags, what admins did and what was emailed."""
    uid = row["id"]
    import admin_billing
    billing = admin_billing.users_block([uid]).get(uid)
    sub = db.query(
        """SELECT status, plan, mrr_cents, currency, trial_end, current_period_end, cancel_at_period_end,
                  comped_until, stripe_customer_id, stripe_subscription_id, started_at, canceled_at, ent_state
             FROM subscriptions WHERE user_id = %s""", (uid,), one=True) or {}
    payments = db.query(
        """SELECT id, status, currency, amount_paid_cents, amount_refunded_cents, plan, billing_reason,
                  paid_at, failed_at, period_start, period_end
             FROM payments WHERE user_id = %s
            ORDER BY COALESCE(paid_at, failed_at, created_at) DESC LIMIT 12""", (uid,)) or []
    lifetime = db.query("""SELECT COALESCE(SUM(amount_paid_cents - amount_refunded_cents), 0) AS cents
                             FROM payments WHERE user_id = %s""", (uid,), one=True) or {"cents": 0}
    logins = db.query(
        """SELECT id, kind, ok, reason, ip::text AS ip, browser, os, device, country, new_network, created_at
             FROM login_events WHERE user_id = %s ORDER BY id DESC LIMIT 10""", (uid,)) or []
    notes = db.query(
        """SELECT n.id, n.body, n.pinned, n.created_at, n.updated_at, a.username AS author
             FROM admin_notes n LEFT JOIN users a ON a.id = n.author_id
            WHERE n.user_id = %s ORDER BY n.pinned DESC, n.created_at DESC""", (uid,)) or []
    tags = db.query(
        """SELECT t.id, t.name, t.color FROM user_admin_tags ut JOIN admin_tags t ON t.id = ut.tag_id
            WHERE ut.user_id = %s ORDER BY lower(t.name)""", (uid,)) or []
    admin_actions = db.query(
        """SELECT a.id, a.action, a.detail, a.by_admin, a.created_at, a.user_id, u.username
             FROM audit_log a LEFT JOIN users u ON u.id = a.user_id
            WHERE a.target_user_id = %s AND a.by_admin ORDER BY a.id DESC LIMIT 20""", (uid,)) or []
    emails = db.query(
        """SELECT id, template, category, status, error, created_at, sent_at
             FROM email_log WHERE user_id = %s ORDER BY id DESC LIMIT 10""", (uid,)) or []
    attr = db.query(
        """SELECT channel, utm_source, utm_medium, utm_campaign, referrer_host, landing_path, first_seen_at
             FROM signup_attribution WHERE user_id = %s""", (uid,), one=True)
    events = db.query(
        """SELECT kind, status_to, plan_to, mrr_from_cents, mrr_to_cents, currency, occurred_at
             FROM subscription_events WHERE user_id = %s AND kind <> 'baseline'
            ORDER BY occurred_at DESC, id DESC LIMIT 20""", (uid,)) or []
    sub = rows_json([sub])[0] if sub else {}
    if sub.get("stripe_subscription_id"):
        sub["stripe_subscription_url"] = f"https://dashboard.stripe.com/subscriptions/{sub['stripe_subscription_id']}"
    if sub.get("stripe_customer_id"):
        sub["stripe_customer_url"] = f"https://dashboard.stripe.com/customers/{sub['stripe_customer_id']}"
    return {
        "billing": billing,
        "subscription": {**sub, "lifetime_cents": int(lifetime["cents"] or 0), "payments": rows_json(payments)},
        "activation": {
            "confirmed_at": iso(row.get("email_verified_at")), "first_upload_at": iso(row.get("first_upload_at")),
            "first_commit_at": iso(row.get("first_commit_at")),
            "activated": bool(row.get("first_commit_at") and row["first_commit_at"] < row["created_at"] + timedelta(days=7)),
        },
        "attribution": rows_json([attr])[0] if attr else None,
        "timeline": timeline(row, rows_json(events), rows_json(payments), rows_json(admin_actions)),
        "logins": rows_json(logins),
        "notes": rows_json(notes),
        "tags": rows_json(tags),
        "admin_actions": rows_json(admin_actions),
        "emails": rows_json(emails),
    }


EVENT_LABEL = {
    "subscribed": "Subscribed", "canceled": "Subscription ended", "plan_changed": "Changed plan",
    "mrr_changed": "Price changed", "status_changed": "Subscription status changed",
    "cancel_scheduled": "Set to cancel at period end", "cancel_unscheduled": "Cancellation withdrawn",
    "trial_started": "Trial started", "trial_extended": "Trial extended", "grace_extended": "Grace period extended",
    "comped": "Given complimentary access", "uncomped": "Complimentary access removed",
}


def timeline(row, events, payments, admin_actions):
    """One newest-first story of this person's life on iSpend, from dates we already hold."""
    items = [{"at": iso(row["created_at"]), "kind": "signup", "label": "Signed up"}]
    if row.get("email_verified_at"):
        items.append({"at": iso(row["email_verified_at"]), "kind": "confirmed", "label": "Confirmed email"})
    if row.get("first_upload_at"):
        items.append({"at": iso(row["first_upload_at"]), "kind": "upload", "label": "Uploaded a first statement"})
    if row.get("first_commit_at"):
        items.append({"at": iso(row["first_commit_at"]), "kind": "import", "label": "Imported a first statement"})
    for e in events:
        label = EVENT_LABEL.get(e["kind"], e["kind"].replace("_", " ").capitalize())
        if e["kind"] == "plan_changed" and e.get("plan_to"):
            label = f"Changed to the {e['plan_to']} plan"
        items.append({"at": e["occurred_at"], "kind": e["kind"], "label": label,
                      "mrr_cents": e["mrr_to_cents"], "currency": e.get("currency")})
    for p in payments:
        if p.get("paid_at"):
            items.append({"at": p["paid_at"], "kind": "payment", "label": "Payment received",
                          "amount_cents": p["amount_paid_cents"], "currency": p["currency"]})
        if p.get("failed_at"):
            items.append({"at": p["failed_at"], "kind": "payment_failed", "label": "Payment failed",
                          "amount_cents": None, "currency": p["currency"]})
    for a in admin_actions:
        items.append({"at": a["created_at"], "kind": "admin", "label": a["action"], "by": a.get("username")})
    items.sort(key=lambda i: i["at"] or "", reverse=True)
    return items[:40]


@bp.get("/users/<int:user_id>/logins")
@admin_required
def logins(user_id):
    cursor = to_int(request.args.get("cursor"), "cursor")
    rows = db.query(
        """SELECT id, kind, ok, reason, ip::text AS ip, browser, os, device, country, new_network, created_at
             FROM login_events WHERE user_id = %s AND (%s::bigint IS NULL OR id < %s)
            ORDER BY id DESC LIMIT 50""", (user_id, cursor, cursor)) or []
    rows = rows_json(rows)
    return jsonify({"items": rows, "next_cursor": rows[-1]["id"] if len(rows) == 50 else None})


# ---------- account actions ----------

def _require(user_id):
    row = _load(user_id)
    if not row:
        return None, api_error("User not found", 404)
    return row, None


@bp.post("/users/<int:user_id>/resend-confirmation")
@admin_required
def resend_confirmation(user_id):
    row, err = _require(user_id)
    if err:
        return err
    if not row.get("email"):
        return api_error("This account has no email address")
    if row.get("email_verified_at"):
        return api_error("This email address is already confirmed", 409)
    auth._send_verification(row)
    audit("user.confirmation_resent", {"id": user_id}, target=user_id)
    return jsonify({"ok": True})


@bp.post("/users/<int:user_id>/mark-confirmed")
@admin_required
def mark_confirmed(user_id):
    row, err = _require(user_id)
    if err:
        return err
    if not row.get("email"):
        return api_error("This account has no email address")
    db.execute("UPDATE users SET email_verified_at = COALESCE(email_verified_at, now()), updated_at = now() "
               "WHERE id = %s", (user_id,))
    audit("user.email_confirmed_by_admin", {"id": user_id}, target=user_id)
    return jsonify({"ok": True})


@bp.put("/users/<int:user_id>/email")
@admin_required
@step_up_required
def change_email(user_id):
    import mailer
    row, err = _require(user_id)
    if err:
        return err
    email = (json_body().get("email") or "").strip().lower()
    problem = auth.email_problem(email)
    if problem:
        return api_error(problem)
    if auth.identity_taken(email, exclude_user_id=user_id):
        return jsonify({"error": "That email is already in use.", "code": "email_taken"}), 409
    db.execute("UPDATE users SET email = %s, email_verified_at = NULL, updated_at = now() WHERE id = %s",
               (email, user_id))
    audit("user.email_change", {"id": user_id, "by_admin": True}, target=user_id)
    token = auth._issue_token(user_id, "verify", auth.VERIFY_TTL)
    mailer.send_async("verify_email", email, user_id=user_id, sent_by=session["user_id"], username=row["username"],
                      link=f"{auth._base_url()}/verify.html?token={token}")
    return jsonify({"ok": True, "email": email})


@bp.post("/users/<int:user_id>/send-password-reset")
@admin_required
def send_password_reset(user_id):
    import mailer
    row, err = _require(user_id)
    if err:
        return err
    if not row.get("email"):
        return api_error("This account has no email address to send a link to")
    if row["status"] != user_state.ACTIVE:
        return api_error("Unlock or restore the account first", 409)
    recent = db.query(
        """SELECT COUNT(*) AS n FROM auth_tokens
            WHERE user_id = %s AND kind = 'reset' AND created_at > now() - interval '1 hour'""",
        (user_id,), one=True)
    if recent["n"] >= auth.MAX_RESET_TOKENS_PER_HOUR:
        return api_error("Several links were sent in the last hour. Try again later.", 429)
    token = auth._issue_token(user_id, "reset", auth.RESET_TTL)
    mailer.send_async("password_reset", row["email"], user_id=user_id, sent_by=session["user_id"],
                      username=row["username"], link=f"{auth._base_url()}/reset.html?token={token}")
    audit("user.password_reset_sent", {"id": user_id}, target=user_id)
    return jsonify({"ok": True})


@bp.post("/users/<int:user_id>/sign-out-all")
@admin_required
def sign_out_all(user_id):
    if user_id == session["user_id"]:
        return api_error("Change your own password to sign your other sessions out")
    row, err = _require(user_id)
    if err:
        return err
    db.execute("UPDATE users SET session_epoch = session_epoch + 1, updated_at = now() WHERE id = %s", (user_id,))
    audit("user.signed_out_everywhere", {"id": user_id}, target=user_id)
    return jsonify({"ok": True})


@bp.post("/users/<int:user_id>/invite")
@admin_required
def send_invite(user_id):
    row, err = _require(user_id)
    if err:
        return err
    return _invite(row)


INVITE_TTL = timedelta(days=7)


def _invite(row):
    """Email a link that sets the account's password. The random password it was created with is
    never shown to anyone."""
    import mailer
    if not row.get("email"):
        return api_error("This account has no email address to invite")
    token = auth._issue_token(row["id"], "invite", INVITE_TTL)
    mailer.send_async("invite", row["email"], user_id=row["id"], sent_by=session["user_id"],
                      username=row["username"], link=f"{auth._base_url()}/reset.html?token={token}&invite=1")
    audit("user.invited", {"id": row["id"]}, target=row["id"])
    return jsonify({"ok": True})


def random_password():
    return secrets.token_urlsafe(24)


# ---------- notes ----------

@bp.post("/users/<int:user_id>/notes")
@admin_required
def add_note(user_id):
    _row, err = _require(user_id)
    if err:
        return err
    data = json_body()
    body = (data.get("body") or "").strip()
    if not 1 <= len(body) <= 4000:
        return api_error("A note is 1 to 4000 characters")
    row = db.execute(
        """INSERT INTO admin_notes (user_id, author_id, body, pinned) VALUES (%s, %s, %s, %s)
           RETURNING id, body, pinned, created_at, updated_at""",
        (user_id, session["user_id"], body, bool(data.get("pinned"))), returning=True)
    audit("admin.note.create", {"id": row["id"], "user": user_id}, target=user_id)
    return jsonify({**rows_json([row])[0], "author": session.get("username")}), 201


@bp.put("/notes/<int:note_id>")
@admin_required
def update_note(note_id):
    data = json_body()
    note = db.query("SELECT id, user_id FROM admin_notes WHERE id = %s", (note_id,), one=True)
    if not note:
        return api_error("Note not found", 404)
    body = data.get("body")
    if body is not None:
        body = body.strip()
        if not 1 <= len(body) <= 4000:
            return api_error("A note is 1 to 4000 characters")
    db.execute("""UPDATE admin_notes SET body = COALESCE(%s, body), pinned = COALESCE(%s, pinned), updated_at = now()
                   WHERE id = %s""",
               (body, data.get("pinned") if isinstance(data.get("pinned"), bool) else None, note_id))
    audit("admin.note.update", {"id": note_id, "user": note["user_id"]}, target=note["user_id"])
    return jsonify({"ok": True})


@bp.delete("/notes/<int:note_id>")
@admin_required
def delete_note(note_id):
    note = db.query("SELECT id, user_id FROM admin_notes WHERE id = %s", (note_id,), one=True)
    if not note:
        return api_error("Note not found", 404)
    db.execute("DELETE FROM admin_notes WHERE id = %s", (note_id,))
    audit("admin.note.delete", {"id": note_id, "user": note["user_id"]}, target=note["user_id"])
    return jsonify({"ok": True})


# ---------- tags ----------

@bp.get("/tags")
@admin_required
def list_tags():
    rows = db.query(
        """SELECT t.id, t.name, t.color, COUNT(ut.user_id) AS users
             FROM admin_tags t LEFT JOIN user_admin_tags ut ON ut.tag_id = t.id
            GROUP BY t.id ORDER BY lower(t.name)""") or []
    return jsonify(rows_json(rows))


def _tag_fields(data, partial=False):
    name = data.get("name")
    color = data.get("color")
    if name is not None or not partial:
        name = (name or "").strip()
        if not 1 <= len(name) <= 40:
            raise ValueError("A tag name is 1 to 40 characters")
    if color is not None and color not in TAG_COLORS:
        raise ValueError("Unknown colour")
    return name, color


@bp.post("/tags")
@admin_required
def create_tag():
    try:
        name, color = _tag_fields(json_body())
    except ValueError as e:
        return api_error(str(e))
    if db.query("SELECT 1 FROM admin_tags WHERE lower(name) = lower(%s)", (name,), one=True):
        return api_error("A tag with that name already exists", 409)
    if color is None:
        used = db.query("SELECT color, COUNT(*) AS n FROM admin_tags GROUP BY color") or []
        counts = {r["color"]: r["n"] for r in used}
        color = min(TAG_COLORS, key=lambda c: (counts.get(c, 0), int(c[1:])))
    row = db.execute("INSERT INTO admin_tags (name, color) VALUES (%s, %s) RETURNING id, name, color",
                     (name, color), returning=True)
    audit("admin.tag.create", {"id": row["id"]})
    return jsonify(row), 201


@bp.put("/tags/<int:tag_id>")
@admin_required
def update_tag(tag_id):
    try:
        name, color = _tag_fields(json_body(), partial=True)
    except ValueError as e:
        return api_error(str(e))
    if name and db.query("SELECT 1 FROM admin_tags WHERE lower(name) = lower(%s) AND id <> %s", (name, tag_id), one=True):
        return api_error("A tag with that name already exists", 409)
    n = db.execute("UPDATE admin_tags SET name = COALESCE(%s, name), color = COALESCE(%s, color) WHERE id = %s",
                   (name or None, color, tag_id))
    if not n:
        return api_error("Tag not found", 404)
    audit("admin.tag.update", {"id": tag_id})
    return jsonify({"ok": True})


@bp.delete("/tags/<int:tag_id>")
@admin_required
def delete_tag(tag_id):
    n = db.execute("DELETE FROM admin_tags WHERE id = %s", (tag_id,))
    if not n:
        return api_error("Tag not found", 404)
    audit("admin.tag.delete", {"id": tag_id})
    return jsonify({"ok": True})


@bp.put("/users/<int:user_id>/tags")
@admin_required
def set_user_tags(user_id):
    _row, err = _require(user_id)
    if err:
        return err
    tag_ids = [int(t) for t in (json_body().get("tag_ids") or []) if str(t).isdigit()]
    with db.transaction():
        db.execute("DELETE FROM user_admin_tags WHERE user_id = %s AND NOT (tag_id = ANY(%s))",
                   (user_id, tag_ids), commit=False)
        for tid in tag_ids:
            db.execute("""INSERT INTO user_admin_tags (user_id, tag_id, created_by)
                          SELECT %s, id, %s FROM admin_tags WHERE id = %s ON CONFLICT DO NOTHING""",
                       (user_id, session["user_id"], tid), commit=False)
    audit("admin.user_tags", {"id": user_id, "tags": len(tag_ids)}, target=user_id)
    return jsonify({"ok": True})


# ---------- saved views (admin preferences) ----------

def clean_admin_views(value):
    """[{id, name, query}] for the admin people list; the query is list parameters only."""
    if not isinstance(value, list):
        raise ValueError("admin_views must be a list")
    if len(value) > 20:
        raise ValueError("At most 20 saved views")
    out, seen = [], set()
    for v in value:
        if not isinstance(v, dict):
            raise ValueError("Each saved view must be an object")
        vid = str(v.get("id") or "")
        if not auth.VIEW_ID.match(vid) or vid in seen:
            raise ValueError("Invalid view id")
        seen.add(vid)
        name = str(v.get("name") or "").strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("View name must be 1-60 characters")
        query = v.get("query")
        if not isinstance(query, str) or not query.startswith("?") or len(query) > 600:
            raise ValueError("View query must be a short filter string")
        bad = set(parse_qs(query[1:], keep_blank_values=True)) - set(FILTER_KEYS)
        if bad:
            raise ValueError(f"View query has unsupported keys: {', '.join(sorted(bad))}")
        out.append({"id": vid, "name": name, "query": query})
    return out

