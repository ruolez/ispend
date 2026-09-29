"""/api/support: a customer's problem reports and the conversation on each one.

Every lookup is scoped to the signed-in customer; someone else's report is a 404. The operator's
side lives in admin_support.py and shares the thread helpers below. Internal notes never leave
this module on the customer's side: they are filtered in SQL, not in the serialiser.
"""

import json
import logging
import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, send_file, session

import db
import support
import support_attachments
import useragent
from auth import login_required
from util import api_error, audit, iso, user_agent

log = logging.getLogger(__name__)

bp = Blueprint("support", __name__, url_prefix="/api/support")

REPORTS_PER_DAY = 10
MESSAGES_PER_HOUR = 40
LIST_LIMIT = 100
_VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")

KIND_LABELS = {"bug": "Something isn't working", "data": "My numbers look wrong", "question": "Question",
               "idea": "Idea or request", "billing": "Billing and account"}
IMPACT_LABELS = {"blocking": "Stops me using iSpend", "annoying": "Annoying, but I can work around it",
                 "minor": "Minor"}

REPORT_FIELDS = """r.id, r.user_id, r.kind, r.subject, r.status, r.impact, r.priority, r.created_at, r.updated_at,
                   r.last_user_at, r.last_admin_at, r.user_seen_at, r.admin_seen_at, r.resolved_at"""


def app_version():
    try:
        with open(_VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip() or "dev"
    except OSError:
        return "dev"


def _uid():
    return session["user_id"]


def _now():
    return datetime.now(timezone.utc)


# ---------- shared with the admin side ----------

def report_json(r):
    return {
        "id": r["id"], "ref": support.ref(r["id"]), "kind": r["kind"], "subject": r["subject"],
        "status": r["status"], "impact": r.get("impact"), "created_at": iso(r.get("created_at")),
        "updated_at": iso(r.get("updated_at")), "last_user_at": iso(r.get("last_user_at")),
        "last_admin_at": iso(r.get("last_admin_at")), "resolved_at": iso(r.get("resolved_at")),
    }


def attachment_json(a, base):
    return {"id": a["id"], "mime": a["mime"], "bytes": a["bytes"], "width": a["width"], "height": a["height"],
            "url": f"{base}/attachments/{a['id']}"}


def thread(report_id, include_internal, attachment_base):
    """Messages oldest first, each with its screenshots."""
    msgs = db.query(
        f"""SELECT m.id, m.author_role, m.internal, m.body, m.created_at, u.username AS author
              FROM support_messages m LEFT JOIN users u ON u.id = m.author_id
             WHERE m.report_id = %s {'' if include_internal else 'AND NOT m.internal'}
             ORDER BY m.id""", (report_id,)) or []
    files = db.query(
        f"""SELECT a.id, a.message_id, a.mime, a.bytes, a.width, a.height FROM support_attachments a
              JOIN support_messages m ON m.id = a.message_id
             WHERE a.report_id = %s {'' if include_internal else 'AND NOT m.internal'}
             ORDER BY a.id""", (report_id,)) or []
    by_message = {}
    for a in files:
        by_message.setdefault(a["message_id"], []).append(attachment_json(a, attachment_base))
    return [{"id": m["id"], "author_role": m["author_role"], "internal": bool(m["internal"]),
             "author": m.get("author") if m["author_role"] == "admin" else None,
             "body": m["body"], "created_at": iso(m["created_at"]),
             "attachments": by_message.get(m["id"], [])} for m in msgs]


def files_from_request():
    """Prepared screenshots from a multipart request, or raises AttachmentError."""
    return support_attachments.prepare_all(request.files.getlist("files"))


def add_message(report, author_id, role, body, prepared, internal=False):
    """Store the files, then the message and its attachment rows in one transaction. A failed
    write removes the files it had just stored. Returns the message id."""
    stored = [support_attachments.store(report["user_id"], p) for p in prepared]
    try:
        with db.transaction():
            msg = db.execute(
                """INSERT INTO support_messages (report_id, author_id, author_role, internal, body)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (report["id"], author_id, role, internal, body), returning=True, commit=False)
            for rel, p in zip(stored, prepared, strict=True):
                db.execute(
                    """INSERT INTO support_attachments (message_id, report_id, user_id, stored_path, mime, bytes, width, height)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (msg["id"], report["id"], report["user_id"], rel, p["mime"], p["bytes"], p["width"], p["height"]),
                    commit=False)
    except Exception:
        support_attachments.remove(stored)
        raise
    return msg["id"]


def send_attachment(row):
    path = support_attachments.abs_path(row["stored_path"])
    ext = os.path.splitext(row["stored_path"])[1]
    try:
        response = send_file(path, mimetype=row["mime"], as_attachment=False,
                             download_name=f"{support.ref(row['report_id'])}-{row['id']}{ext}", max_age=0)
    except FileNotFoundError:
        return api_error("That screenshot is no longer available", 404)
    response.headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
    return response


def admin_recipients():
    """Who hears about new reports and replies: nobody when the operator turned it off."""
    if (db.get_setting("support_notify") or "1") != "1":
        return []
    return admin_emails()


def admin_emails():
    return db.query(
        """SELECT id, username, email FROM users
            WHERE role = 'admin' AND status = 'active' AND email IS NOT NULL AND email <> ''
            ORDER BY id""") or []


def base_url():
    import auth
    return auth._base_url()


def notify_admins(report, customer, event, message):
    import mailer
    try:
        for admin in admin_recipients():
            mailer.send_async(
                "support_admin_new", admin["email"], user_id=admin["id"],
                ref=support.ref(report["id"]), subject=report["subject"], username=customer["username"],
                event=event, kind=KIND_LABELS.get(report["kind"], report["kind"]),
                impact=IMPACT_LABELS.get(report.get("impact"), "Not given"),
                message=message[:2000], link=f"{base_url()}/admin#support/{report['id']}")
    except Exception:
        log.warning("could not queue the support notification", exc_info=True)


# ---------- the customer's endpoints ----------

def _own(report_id):
    return db.query(f"SELECT {REPORT_FIELDS} FROM support_reports r WHERE r.id = %s AND r.user_id = %s",
                    (report_id, _uid()), one=True)


def _me():
    return db.query("SELECT id, username, email FROM users WHERE id = %s", (_uid(),), one=True) or {}


def _detail(report):
    return {**report_json(report),
            "can_reopen": report["status"] != "resolved" or support.can_reopen(report.get("resolved_at"), _now()),
            "messages": thread(report["id"], False, "/api/support")}


def _fields():
    """Form fields from multipart (with screenshots) or a JSON body."""
    if request.mimetype == "multipart/form-data":
        return request.form.to_dict()
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _context(fields):
    raw = fields.get("context")
    if raw in (None, ""):
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    context = support.clean_context(raw)
    if context:
        context["app_version"] = app_version()
        context.update({k: v for k, v in useragent.parse(user_agent() or "").items() if v})
    return context


@bp.get("/meta")
@login_required
def meta():
    return jsonify({"version": app_version(), "support_email": (db.get_setting("support_email") or "").strip() or None,
                    "reopen_days": support.REOPEN_DAYS, "max_files": support_attachments.MAX_FILES,
                    "max_file_mb": support_attachments.MAX_BYTES // (1024 * 1024)})


@bp.get("/reports")
@login_required
def list_reports():
    rows = db.query(
        f"""SELECT {REPORT_FIELDS},
                   (SELECT COUNT(*) FROM support_messages m WHERE m.report_id = r.id AND NOT m.internal) AS message_count
              FROM support_reports r WHERE r.user_id = %s
             ORDER BY r.updated_at DESC, r.id DESC LIMIT %s""", (_uid(), LIST_LIMIT)) or []
    return jsonify([{**report_json(r), "message_count": r["message_count"],
                     "unread": bool(r.get("last_admin_at") and (r.get("user_seen_at") is None
                                                                or r["last_admin_at"] > r["user_seen_at"]))}
                    for r in rows])


@bp.post("/reports")
@login_required
def create_report():
    uid = _uid()
    fields = _fields()
    clean, problem = support.validate_report(fields)
    if problem:
        return api_error(problem)
    recent = db.query("""SELECT COUNT(*) AS n FROM support_reports
                          WHERE user_id = %s AND created_at > now() - interval '1 day'""", (uid,), one=True)
    if recent and recent["n"] >= REPORTS_PER_DAY:
        return api_error("You have sent a lot of reports today. Add to one of them instead, or try again tomorrow.", 429)
    try:
        prepared = files_from_request()
    except support_attachments.AttachmentError as e:
        return api_error(str(e))
    context = _context(fields)
    with db.transaction():
        report = db.execute(
            f"""INSERT INTO support_reports (user_id, kind, subject, impact, context, user_seen_at)
                VALUES (%s, %s, %s, %s, %s, now()) RETURNING {REPORT_FIELDS.replace('r.', '')}""",
            (uid, clean["kind"], clean["subject"], clean["impact"], support.context_json(context)),
            returning=True, commit=False)
        add_message(report, uid, "user", clean["body"], prepared)
    audit("support.report", {"id": report["id"], "kind": clean["kind"], "files": len(prepared)})
    me = _me()
    if me.get("email"):
        import mailer
        mailer.send_async("support_received", me["email"], user_id=uid, username=me["username"],
                          ref=support.ref(report["id"]), subject=clean["subject"],
                          link=f"{base_url()}/help.html?report={report['id']}")
    notify_admins(report, me, "sent a new report", clean["body"])
    return jsonify(_detail(_own(report["id"]))), 201


@bp.get("/reports/<int:report_id>")
@login_required
def get_report(report_id):
    report = _own(report_id)
    if not report:
        return api_error("Report not found", 404)
    db.execute("UPDATE support_reports SET user_seen_at = now() WHERE id = %s AND user_id = %s", (report_id, _uid()))
    return jsonify(_detail(report))


@bp.post("/reports/<int:report_id>/messages")
@login_required
def reply(report_id):
    uid = _uid()
    report = _own(report_id)
    if not report:
        return api_error("Report not found", 404)
    fields = _fields()
    has_files = bool([f for f in request.files.getlist("files") if f])
    body, problem = support.validate_message(fields, has_files=has_files)
    if problem:
        return api_error(problem)
    status = support.next_status(report["status"], "user", "reply", report.get("resolved_at"), _now())
    if status is None:
        return api_error(f"This report was resolved more than {support.REOPEN_DAYS} days ago. "
                         f"Start a new report and mention {support.ref(report_id)}.", 409)
    recent = db.query(
        """SELECT COUNT(*) AS n FROM support_messages m JOIN support_reports r ON r.id = m.report_id
            WHERE r.user_id = %s AND m.author_role = 'user' AND m.created_at > now() - interval '1 hour'""",
        (uid,), one=True)
    if recent and recent["n"] >= MESSAGES_PER_HOUR:
        return api_error("That is a lot of messages in an hour. Try again in a little while.", 429)
    try:
        prepared = files_from_request()
    except support_attachments.AttachmentError as e:
        return api_error(str(e))
    with db.transaction():
        add_message(report, uid, "user", body, prepared)
        db.execute(
            """UPDATE support_reports SET status = %s, updated_at = now(), last_user_at = now(), user_seen_at = now(),
                      resolved_at = CASE WHEN %s = 'resolved' THEN resolved_at END
                WHERE id = %s AND user_id = %s""", (status, status, report_id, uid), commit=False)
    audit("support.reply", {"id": report_id, "files": len(prepared), "reopened": report["status"] == "resolved"})
    notify_admins(report, _me(), "reopened the report" if report["status"] == "resolved" else "replied", body)
    return jsonify(_detail(_own(report_id)))


@bp.post("/reports/<int:report_id>/resolve")
@login_required
def resolve(report_id):
    report = _own(report_id)
    if not report:
        return api_error("Report not found", 404)
    if report["status"] != "resolved":
        db.execute("""UPDATE support_reports SET status = 'resolved', resolved_at = now(), updated_at = now()
                       WHERE id = %s AND user_id = %s""", (report_id, _uid()))
        audit("support.resolve", {"id": report_id, "by": "user"})
    return jsonify(_detail(_own(report_id)))


@bp.get("/attachments/<int:attachment_id>")
@login_required
def attachment(attachment_id):
    row = db.query(
        """SELECT a.id, a.report_id, a.stored_path, a.mime FROM support_attachments a
             JOIN support_messages m ON m.id = a.message_id
            WHERE a.id = %s AND a.user_id = %s AND NOT m.internal""", (attachment_id, _uid()), one=True)
    if not row:
        return api_error("Screenshot not found", 404)
    return send_attachment(row)
