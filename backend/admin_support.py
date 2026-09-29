"""/api/admin/support: the operator's side of problem reports.

The inbox is organised by whose move it is: "needs reply" (open or in progress — the operator
owes an answer), "waiting" (answered, the customer's move) and "resolved". A thread shows the
operator's internal notes alongside the conversation, the customer's diagnostics, and the server
errors recorded for that customer around the time of the report, so a "Report" from an error toast
lands next to the traceback it came from.
"""

import json
import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, session

import db
import support
import support_api
import support_attachments
from auth import admin_required, email_problem
from util import api_error, audit, iso, json_body, rows_json

log = logging.getLogger(__name__)

bp = Blueprint("admin_support", __name__, url_prefix="/api/admin/support")

VIEWS = {
    "needs_reply": "r.status IN ('open', 'in_progress')",
    "waiting": "r.status = 'waiting'",
    "resolved": "r.status = 'resolved'",
    "all": "TRUE",
}
# Whoever has waited longest comes first; urgent before everything else.
ORDER = {
    "needs_reply": "COALESCE(r.priority, 4), r.last_user_at ASC, r.id",
    "waiting": "r.last_admin_at ASC NULLS FIRST, r.id",
    "resolved": "r.resolved_at DESC NULLS LAST, r.id DESC",
    "all": "r.updated_at DESC, r.id DESC",
}
PAGE_SIZE = 50
ATTACHMENTS = "/api/admin/support"
SAVED_REPLIES_MAX = 30
SAVED_TITLE_MAX = 60
SAVED_BODY_MAX = 4000
ERROR_WINDOW = "24 hours"


def needs_reply_count():
    row = db.query(f"SELECT COUNT(*) AS n FROM support_reports r WHERE {VIEWS['needs_reply']}", one=True)
    return int((row or {}).get("n") or 0)


def _filters(args):
    where, params = [], {}
    kind = args.get("kind")
    if kind in support.KINDS:
        where.append("r.kind = %(kind)s")
        params["kind"] = kind
    priority = args.get("priority")
    if priority in ("1", "2", "3"):
        where.append("r.priority = %(priority)s")
        params["priority"] = int(priority)
    elif priority == "none":
        where.append("r.priority IS NULL")
    user_id = args.get("user_id")
    if user_id and str(user_id).isdigit():
        where.append("r.user_id = %(user_id)s")
        params["user_id"] = int(user_id)
    q = (args.get("q") or "").strip()
    if q:
        rid = support.parse_ref(q)
        params["q"] = f"%{q}%"
        if rid:
            params["rid"] = rid
            where.append("(r.id = %(rid)s OR r.subject ILIKE %(q)s)")
        else:
            where.append("(r.subject ILIKE %(q)s OR u.username ILIKE %(q)s OR u.email ILIKE %(q)s)")
    return where, params


@bp.get("/reports")
@admin_required
def list_reports():
    view = request.args.get("view") or "needs_reply"
    if view not in VIEWS:
        return api_error("Unknown view")
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    where, params = _filters(request.args)
    base = " AND ".join(where) or "TRUE"
    counts = db.query(
        f"""SELECT {", ".join(f"COUNT(*) FILTER (WHERE {cond}) AS {name}" for name, cond in VIEWS.items())}
              FROM support_reports r JOIN users u ON u.id = r.user_id WHERE {base}""", params, one=True) or {}
    rows = db.query(
        f"""SELECT {support_api.REPORT_FIELDS}, u.username, u.email,
                   (SELECT COUNT(*) FROM support_messages m WHERE m.report_id = r.id AND NOT m.internal) AS message_count,
                   (SELECT COUNT(*) FROM support_attachments a WHERE a.report_id = r.id) AS attachment_count,
                   (SELECT left(m.body, 160) FROM support_messages m
                     WHERE m.report_id = r.id AND NOT m.internal ORDER BY m.id DESC LIMIT 1) AS preview
              FROM support_reports r JOIN users u ON u.id = r.user_id
             WHERE {VIEWS[view]} AND {base}
             ORDER BY {ORDER[view]} LIMIT %(limit)s OFFSET %(offset)s""",
        {**params, "limit": PAGE_SIZE + 1, "offset": (page - 1) * PAGE_SIZE}) or []
    return jsonify({
        "view": view, "page": page, "has_more": len(rows) > PAGE_SIZE,
        "counts": {name: int(counts.get(name) or 0) for name in VIEWS},
        "reports": [_row_json(r) for r in rows[:PAGE_SIZE]],
    })


def _row_json(r):
    return {**support_api.report_json(r), "priority": r.get("priority"),
            "user": {"id": r["user_id"], "username": r.get("username"), "email": r.get("email")},
            "message_count": r.get("message_count", 0), "attachment_count": r.get("attachment_count", 0),
            "preview": r.get("preview"),
            "unread": r.get("admin_seen_at") is None or r["last_user_at"] > r["admin_seen_at"]}


def _load(report_id):
    return db.query(
        f"""SELECT {support_api.REPORT_FIELDS}, r.context, u.username, u.email, u.status AS user_status,
                   u.created_at AS user_created_at
              FROM support_reports r JOIN users u ON u.id = r.user_id WHERE r.id = %s""", (report_id,), one=True)


def _request_ids(context):
    ids = {context.get("request_id")} | {r.get("request_id") for r in context.get("requests") or []}
    return sorted(i for i in ids if i)


def _server_errors(report, request_ids):
    rows = db.query(
        """SELECT id, error_type, message, location, status, request_id, created_at FROM app_errors
            WHERE (user_id = %(u)s AND created_at BETWEEN %(at)s - interval '24 hours' AND %(at)s + interval '1 hour')
               OR request_id = ANY(%(ids)s)
            ORDER BY created_at DESC LIMIT 20""",
        {"u": report["user_id"], "at": report["created_at"], "ids": request_ids}) or []
    return [{**r, "matches_report": bool(r.get("request_id") and r["request_id"] in request_ids)} for r in rows_json(rows)]


def _detail(report):
    import admin_billing
    context = report.get("context") or {}
    ids = _request_ids(context)
    others = db.query(
        f"""SELECT {support_api.REPORT_FIELDS} FROM support_reports r
             WHERE r.user_id = %s AND r.id <> %s ORDER BY r.updated_at DESC LIMIT 10""",
        (report["user_id"], report["id"])) or []
    return {
        **support_api.report_json(report), "priority": report.get("priority"), "context": context,
        "customer": {"id": report["user_id"], "username": report["username"], "email": report.get("email"),
                     "status": report.get("user_status"), "created_at": iso(report.get("user_created_at")),
                     "billing": admin_billing.users_block([report["user_id"]]).get(report["user_id"])},
        "other_reports": [support_api.report_json(r) for r in others],
        "server_errors": _server_errors(report, ids),
        "messages": support_api.thread(report["id"], True, ATTACHMENTS),
    }


@bp.get("/reports/<int:report_id>")
@admin_required
def get_report(report_id):
    report = _load(report_id)
    if not report:
        return api_error("Report not found", 404)
    db.execute("UPDATE support_reports SET admin_seen_at = now() WHERE id = %s", (report_id,))
    return jsonify(_detail(report))


def _truthy(value):
    return value is True or str(value).lower() in ("1", "true", "on", "yes")


def _email_customer(report, template, message):
    if not report.get("email"):
        return
    import mailer
    try:
        mailer.send_async(template, report["email"], user_id=report["user_id"], sent_by=session.get("user_id"),
                          username=report["username"], ref=support.ref(report["id"]), subject=report["subject"],
                          message=message[:4000], days=support.REOPEN_DAYS,
                          link=f"{support_api.base_url()}/help.html?report={report['id']}")
    except Exception:
        log.warning("could not queue the support reply email", exc_info=True)


@bp.post("/reports/<int:report_id>/messages")
@admin_required
def reply(report_id):
    report = _load(report_id)
    if not report:
        return api_error("Report not found", 404)
    fields = support_api._fields()
    internal = _truthy(fields.get("internal"))
    resolve = _truthy(fields.get("resolve")) and not internal
    has_files = bool([f for f in request.files.getlist("files") if f])
    body, problem = support.validate_message(fields, has_files=has_files)
    if problem:
        return api_error(problem)
    try:
        prepared = support_api.files_from_request()
    except support_attachments.AttachmentError as e:
        return api_error(str(e))
    action = "note" if internal else ("reply_resolve" if resolve else "reply")
    status = support.next_status(report["status"], "admin", action, report.get("resolved_at"), datetime.now(timezone.utc))
    with db.transaction():
        support_api.add_message(report, session["user_id"], "admin", body, prepared, internal=internal)
        if internal:
            db.execute("UPDATE support_reports SET admin_seen_at = now() WHERE id = %s", (report_id,), commit=False)
        else:
            db.execute(
                """UPDATE support_reports SET status = %s, updated_at = now(), last_admin_at = now(), admin_seen_at = now(),
                          resolved_at = CASE WHEN %s = 'resolved' THEN COALESCE(resolved_at, now()) END
                    WHERE id = %s""", (status, status, report_id), commit=False)
    audit("support.admin_reply", {"report": report_id, "internal": internal, "resolved": resolve,
                                  "files": len(prepared)}, target=report["user_id"])
    if not internal:
        _email_customer(report, "support_resolved" if resolve else "support_reply", body)
    return jsonify(_detail(_load(report_id)))


@bp.patch("/reports/<int:report_id>")
@admin_required
def update(report_id):
    report = _load(report_id)
    if not report:
        return api_error("Report not found", 404)
    data = json_body()
    sets, params = [], []
    if "status" in data:
        status = data.get("status")
        if status not in support.STATUSES:
            return api_error("Invalid status")
        sets += ["status = %s", "resolved_at = CASE WHEN %s = 'resolved' THEN COALESCE(resolved_at, now()) END"]
        params += [status, status]
    if "priority" in data:
        priority = data.get("priority")
        if priority is not None and (isinstance(priority, bool) or priority not in (1, 2, 3)):
            return api_error("Priority must be 1, 2, 3 or empty")
        sets.append("priority = %s")
        params.append(priority)
    if not sets:
        return api_error("Nothing to change")
    db.execute(f"UPDATE support_reports SET {', '.join(sets)}, updated_at = now() WHERE id = %s", (*params, report_id))
    changed = [k for k in ("status", "priority") if k in data]
    audit("support.update", {"report": report_id, "fields": changed, "status": data.get("status")},
          target=report["user_id"])
    if data.get("status") == "resolved" and report["status"] != "resolved":
        _email_customer(report, "support_resolved", "")
    return jsonify(_detail(_load(report_id)))


@bp.get("/attachments/<int:attachment_id>")
@admin_required
def attachment(attachment_id):
    row = db.query("SELECT id, report_id, stored_path, mime FROM support_attachments WHERE id = %s",
                   (attachment_id,), one=True)
    if not row:
        return api_error("Screenshot not found", 404)
    return support_api.send_attachment(row)


# ---------- settings ----------

def saved_replies():
    try:
        value = json.loads(db.get_setting("support_saved_replies") or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _clean_saved(value):
    if not isinstance(value, list) or len(value) > SAVED_REPLIES_MAX:
        return None, f"Keep at most {SAVED_REPLIES_MAX} saved replies"
    out = []
    for item in value:
        if not isinstance(item, dict):
            return None, "Invalid saved reply"
        title = " ".join(str(item.get("title") or "").split())
        body = support.clean_text(item.get("body"))
        if not title or not body:
            return None, "Each saved reply needs a name and a message"
        if len(title) > SAVED_TITLE_MAX or len(body) > SAVED_BODY_MAX:
            return None, f"Saved replies are limited to {SAVED_TITLE_MAX}-character names and {SAVED_BODY_MAX}-character messages"
        out.append({"title": title, "body": body})
    return out, None


def _settings_json():
    return {"support_email": db.get_setting("support_email") or "",
            "notify": (db.get_setting("support_notify") or "1") == "1",
            "saved_replies": saved_replies(),
            "notify_to": [r["email"] for r in support_api.admin_emails()]}


@bp.get("/settings")
@admin_required
def get_settings():
    return jsonify(_settings_json())


@bp.put("/settings")
@admin_required
def put_settings():
    data = json_body()
    if "support_email" in data:
        email = (data.get("support_email") or "").strip()
        if email and email_problem(email):
            return api_error(email_problem(email))
        db.set_setting("support_email", email)
    if "notify" in data:
        db.set_setting("support_notify", "1" if data.get("notify") else "0")
    if "saved_replies" in data:
        clean, problem = _clean_saved(data.get("saved_replies"))
        if problem:
            return api_error(problem)
        db.set_setting("support_saved_replies", json.dumps(clean))
    audit("support.settings", {"keys": sorted(k for k in data if k in ("support_email", "notify", "saved_replies"))})
    return jsonify(_settings_json())
