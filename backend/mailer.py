"""Outbound email over stdlib smtplib. No new dependency.

Unconfigured is a supported state: every send becomes a logged no-op, so a fresh install and the
dev environment work untouched and nothing in the app ever fails because email is not set up.
"""

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

import config
import db
import jobs

log = logging.getLogger(__name__)

CONFIG_KEYS = ["smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password",
               "smtp_from_email", "smtp_from_name"]
SECRET_KEYS = {"smtp_password"}
SECURITY_MODES = ("starttls", "ssl", "none")
TIMEOUT = 15


def _value(key, env_value):
    return env_value or db.get_setting(key) or ""


def settings():
    return {
        "smtp_host": _value("smtp_host", config.SMTP_HOST),
        "smtp_port": _value("smtp_port", config.SMTP_PORT),
        "smtp_security": _value("smtp_security", config.SMTP_SECURITY) or "starttls",
        "smtp_user": _value("smtp_user", config.SMTP_USER),
        "smtp_password": _value("smtp_password", config.SMTP_PASSWORD),
        "smtp_from_email": _value("smtp_from_email", config.SMTP_FROM_EMAIL),
        "smtp_from_name": _value("smtp_from_name", config.SMTP_FROM_NAME) or "iSpend",
    }


def env_locked(key):
    return bool({"smtp_host": config.SMTP_HOST, "smtp_port": config.SMTP_PORT,
                 "smtp_security": config.SMTP_SECURITY, "smtp_user": config.SMTP_USER,
                 "smtp_password": config.SMTP_PASSWORD,
                 "smtp_from_email": config.SMTP_FROM_EMAIL,
                 "smtp_from_name": config.SMTP_FROM_NAME}.get(key))


def configured():
    try:
        s = settings()
    except Exception:
        return False
    return bool(s["smtp_host"] and s["smtp_from_email"])


def _port(s):
    if s["smtp_port"]:
        try:
            return int(s["smtp_port"])
        except ValueError:
            pass
    return {"ssl": 465, "none": 25}.get(s["smtp_security"], 587)


def _deliver(to, subject, text, html=None):
    """(ok, error, message_id). Never raises: a failed send must not take a request or a job down."""
    if not configured():
        log.warning("SMTP is not configured — skipping %r to %s", subject, _redact(to))
        return False, "not configured", None
    s = settings()
    msg = EmailMessage()
    msg["From"] = formataddr((s["smtp_from_name"], s["smtp_from_email"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text)                       # plain text first
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        port, mode = _port(s), s["smtp_security"]
        cls = smtplib.SMTP_SSL if mode == "ssl" else smtplib.SMTP
        with cls(s["smtp_host"], port, timeout=TIMEOUT) as server:
            if mode == "starttls":
                server.starttls()
            if s["smtp_user"]:
                server.login(s["smtp_user"], s["smtp_password"])
            server.send_message(msg)
        return True, None, msg["Message-ID"]
    except Exception as e:
        log.warning("SMTP send failed (%s): %s", subject, e)
        return False, str(e), None


def send(to, subject, text, html=None):
    """(ok, error)."""
    ok, err, _mid = _deliver(to, subject, text, html)
    return ok, err


def _redact(address):
    """Keep logs from becoming a mailing list."""
    return "***@" + address.split("@", 1)[1] if "@" in (address or "") else "***"


# What kind of message each template is, for the admin's delivery report.
CATEGORIES = {
    "welcome": "auth", "welcome_pending": "auth", "verify_email": "auth", "password_reset": "auth",
    "password_changed": "auth", "invite": "auth", "admin_new_login": "admin",
    "trial_ending": "lifecycle", "trial_ended": "lifecycle", "grace_ending": "lifecycle", "read_only": "lifecycle",
    "payment_failed": "billing", "subscription_started": "billing", "subscription_canceled": "billing",
    "test": "test",
}


def _log_queued(template, to, user_id=None, sent_by=None, category=None):
    """One email_log row per message, written before the send so a message lost with its worker
    still shows up (as queued) instead of vanishing."""
    try:
        row = db.execute(
            """INSERT INTO email_log (user_id, to_address, template, category, sent_by)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (user_id, to, template, category or CATEGORIES.get(template, "admin"), sent_by), returning=True)
        return row["id"] if isinstance(row, dict) else None
    except Exception:
        log.warning("could not record an outgoing email", exc_info=True)
        return None


def _log_result(log_id, subject, ok, err, message_id):
    if not log_id:
        return
    status = "sent" if ok else ("skipped" if err == "not configured" else "failed")
    try:
        db.execute(
            """UPDATE email_log SET status = %s, subject = %s, error = %s, message_id = %s,
                      sent_at = CASE WHEN %s THEN now() ELSE NULL END
                WHERE id = %s""",
            (status, subject[:300], (err or "")[:500] or None, message_id, ok, log_id))
    except Exception:
        log.warning("could not record an email result", exc_info=True)


def send_template(template, to, user_id=None, sent_by=None, log_id=None, **ctx):
    """Render and send now. Every attempt lands in email_log."""
    import email_templates

    subject, text, html = email_templates.render(template, **ctx)
    if log_id is None:
        log_id = _log_queued(template, to, user_id, sent_by)
    ok, err, message_id = _deliver(to, subject, text, html)
    _log_result(log_id, subject, ok, err, message_id)
    return ok, err


def _send_job(template, to, ctx, log_id):
    send_template(template, to, log_id=log_id, **ctx)


def send_async(template, to, user_id=None, sent_by=None, **ctx):
    """Outbound SMTP must never sit in a request's critical path — a slow relay would make signup
    look broken."""
    log_id = _log_queued(template, to, user_id, sent_by)
    jobs.spawn(_send_job, template, to, ctx, log_id)
