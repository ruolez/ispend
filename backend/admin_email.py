"""Admin › Messages: write to one person, or to everyone matching a people-list filter.

Two kinds, chosen on every message: 'service' (about their account — an outage, a change to the
terms) goes to everyone addressed; 'marketing' (news, offers) goes only to confirmed addresses that
have not unsubscribed, and carries a one-click unsubscribe (RFC 8058) in the headers and the footer.

Sending runs on its own single-thread executor, never the import pool, throttled, one email_log row
per recipient. Each row is claimed ('sending') before it goes out, so a campaign picked up again
after a worker restart never sends the same message twice.
"""

import hashlib
import hmac
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs

from flask import Blueprint, current_app, jsonify, session

import config
import db
import email_templates
import mailer
from auth import admin_required, step_up_missing
from util import api_error, audit, json_body, rows_json

log = logging.getLogger(__name__)

bp = Blueprint("admin_email", __name__, url_prefix="/api/admin")

MAX_RECIPIENTS = 5000
BIG_AUDIENCE = 200
SEND_INTERVAL = 0.5
STALE_MINUTES = 10
CATEGORIES = ("service", "marketing")
TEMPLATES_KEY = "admin_email_templates"
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="campaign")


def email_hash(address):
    return hashlib.sha256((address or "").strip().lower().encode()).hexdigest()


def unsubscribe_token(uid):
    sig = hmac.new(config.SECRET_KEY.encode(), f"unsubscribe:{uid}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{uid}.{sig}"


def user_for_token(token):
    try:
        uid, sig = str(token).split(".", 1)
        uid = int(uid)
    except (ValueError, TypeError):
        return None
    return uid if hmac.compare_digest(unsubscribe_token(uid), f"{uid}.{sig}") else None


def _base():
    import auth
    try:
        return auth._base_url()
    except Exception:
        return (config.APP_BASE_URL or "").rstrip("/")


# ---------- audience ----------

def audience_ids(audience):
    import admin_users
    if isinstance(audience.get("ids"), list):
        return [int(i) for i in audience["ids"] if str(i).isdigit()][:MAX_RECIPIENTS]
    query = audience.get("query")
    if isinstance(query, str):
        args = {k: v[0] for k, v in parse_qs(query.lstrip("?")).items() if k in admin_users.FILTER_KEYS}
        ids, _total = admin_users.matching_ids(args, MAX_RECIPIENTS)
        return ids
    return []


def resolve(audience, category):
    """(send_to, skipped) where skipped counts why people were left out."""
    ids = audience_ids(audience)
    if not ids:
        return [], {}
    rows = db.query(
        """SELECT u.id, u.username, u.email, u.status, u.email_verified_at, s.trial_end,
                  EXISTS (SELECT 1 FROM email_suppressions x WHERE x.email_sha256 = encode(sha256(lower(u.email)::bytea), 'hex')) AS suppressed
             FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id
            WHERE u.id = ANY(%s) AND u.role = 'user'""", (ids,)) or []
    send, skipped = [], {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    for r in rows:
        if not r.get("email"):
            skip("no_email")
        elif r["status"] != "active":
            skip("not_active")
        elif category == "marketing" and r.get("suppressed"):
            skip("unsubscribed")
        elif category == "marketing" and not r.get("email_verified_at"):
            skip("unconfirmed")
        else:
            send.append(r)
    return send, skipped


def _context(r):
    # There is no separate "name": sign-ups use their email as the username, so greet the part
    # before the @ rather than a whole address.
    name = (r.get("username") or "").split("@")[0]
    return {"name": name, "email": r.get("email") or "",
            "trial_end": r["trial_end"].strftime("%d %B %Y") if r.get("trial_end") else "", "app_link": _base()}


def _footer(category, uid):
    if category != "marketing":
        return "You are receiving this because you have an iSpend account."
    return f"Don't want news from iSpend? Unsubscribe with one click: {_base()}/api/public/unsubscribe?t={unsubscribe_token(uid)}"


def render_for(r, subject, body, category):
    return email_templates.render_custom(subject, body, _context(r), _footer(category, r["id"]))


def _validate(data):
    subject = (data.get("subject") or "").strip()
    body = (data.get("body") or "").strip()
    category = data.get("category")
    if not 1 <= len(subject) <= 200:
        raise ValueError("A subject is 1 to 200 characters")
    if not 1 <= len(body) <= 20000:
        raise ValueError("Write a message first")
    if category not in CATEGORIES:
        raise ValueError("Choose whether this is about their account or news")
    audience = data.get("audience") or {}
    if not isinstance(audience, dict):
        raise ValueError("Unknown audience")
    return subject, body, category, audience


# ---------- routes ----------

@bp.post("/email/preview")
@admin_required
def preview():
    try:
        subject, body, category, audience = _validate(json_body())
    except ValueError as e:
        return api_error(str(e))
    send, skipped = resolve(audience, category)
    sample = None
    if send:
        s_subject, text, _html = render_for(send[0], subject, body, category)
        sample = {"to": send[0]["email"], "subject": s_subject, "text": text}
    return jsonify({"recipients": len(send), "skipped": skipped, "sample": sample,
                    "big": len(send) > BIG_AUDIENCE, "configured": mailer.configured()})


@bp.post("/email/send")
@admin_required
def send():
    data = json_body()
    try:
        subject, body, category, audience = _validate(data)
    except ValueError as e:
        return api_error(str(e))
    if data.get("test"):
        me = db.query("""SELECT u.id, u.username, u.email, s.trial_end FROM users u
                           LEFT JOIN subscriptions s ON s.user_id = u.id WHERE u.id = %s""", (session["user_id"],), one=True)
        if not me or not me.get("email"):
            return api_error("Add an email address to your own account to send yourself a test")
        s_subject, text, html = render_for(me, subject, body, category)
        ok, err, _mid = mailer._deliver(me["email"], f"[Test] {s_subject}", text, html)
        return jsonify({"ok": ok, "error": err, "to": me["email"]})
    recipients, skipped = resolve(audience, category)
    if not recipients:
        return api_error("Nobody in this audience can receive it")
    if len(recipients) > 1:
        blocked = step_up_missing()
        if blocked:
            return blocked
    campaign_id = create_campaign(subject, body, category, audience, recipients, skipped)
    audit("admin.email.send", {"campaign": campaign_id, "recipients": len(recipients), "category": category})
    start(campaign_id)
    return jsonify({"id": campaign_id, "recipients": len(recipients), "skipped": skipped}), 202


@bp.post("/users/<int:user_id>/email")
@admin_required
def email_one(user_id):
    """A message to one person: the same pipeline, an audience of one."""
    data = json_body()
    data["audience"] = {"ids": [user_id]}
    try:
        subject, body, category, audience = _validate(data)
    except ValueError as e:
        return api_error(str(e))
    recipients, skipped = resolve(audience, category)
    if not recipients:
        why = next(iter(skipped), "no_email")
        return api_error({"no_email": "This person has no email address", "not_active": "This account is locked or deleted",
                          "unsubscribed": "They unsubscribed from news; send it as an account message if it is one",
                          "unconfirmed": "Their email address is not confirmed yet"}.get(why, "They cannot receive it"), 409)
    campaign_id = create_campaign(subject, body, category, audience, recipients, skipped)
    audit("admin.email.send", {"campaign": campaign_id, "recipients": 1, "category": category}, target=user_id)
    start(campaign_id)
    return jsonify({"id": campaign_id}), 202


@bp.get("/email/campaigns")
@admin_required
def campaigns():
    rows = db.query(
        """SELECT c.id, c.subject, c.category, c.status, c.recipients, c.created_at, c.finished_at, u.username AS author,
                  COUNT(l.id) FILTER (WHERE l.status = 'sent') AS sent,
                  COUNT(l.id) FILTER (WHERE l.status = 'failed') AS failed,
                  COUNT(l.id) FILTER (WHERE l.status IN ('queued', 'sending')) AS pending
             FROM email_campaigns c LEFT JOIN users u ON u.id = c.created_by
             LEFT JOIN email_log l ON l.campaign_id = c.id
            GROUP BY c.id, u.username ORDER BY c.id DESC LIMIT 50""") or []
    return jsonify(rows_json(rows))


@bp.get("/email/campaigns/<int:campaign_id>")
@admin_required
def campaign_detail(campaign_id):
    row = db.query("SELECT * FROM email_campaigns WHERE id = %s", (campaign_id,), one=True)
    if not row:
        return api_error("Not found", 404)
    counts = db.query("SELECT status, COUNT(*) AS n FROM email_log WHERE campaign_id = %s GROUP BY 1", (campaign_id,)) or []
    failures = db.query("""SELECT l.user_id, u.username, l.error FROM email_log l LEFT JOIN users u ON u.id = l.user_id
                            WHERE l.campaign_id = %s AND l.status = 'failed' ORDER BY l.id LIMIT 20""", (campaign_id,)) or []
    return jsonify({**rows_json([row])[0], "counts": {r["status"]: r["n"] for r in counts}, "failures": rows_json(failures)})


@bp.post("/email/campaigns/<int:campaign_id>/cancel")
@admin_required
def cancel(campaign_id):
    n = db.execute("UPDATE email_campaigns SET status = 'canceled', finished_at = now() WHERE id = %s AND status = 'sending'",
                   (campaign_id,))
    if not n:
        return api_error("That message has already finished sending", 409)
    db.execute("""UPDATE email_log SET status = 'skipped', error = 'cancelled'
                   WHERE campaign_id = %s AND status = 'queued'""", (campaign_id,))
    audit("admin.email.cancel", {"campaign": campaign_id})
    return jsonify({"ok": True})


@bp.get("/email/templates")
@admin_required
def get_templates():
    try:
        return jsonify(json.loads(db.get_setting(TEMPLATES_KEY) or "[]"))
    except ValueError:
        return jsonify([])


@bp.put("/email/templates")
@admin_required
def put_templates():
    items = json_body().get("templates")
    if not isinstance(items, list) or len(items) > 20:
        return api_error("Up to 20 saved messages")
    clean = []
    for t in items:
        if not isinstance(t, dict):
            return api_error("Each saved message is an object")
        name = str(t.get("name") or "").strip()[:60]
        if not name:
            return api_error("Each saved message needs a name")
        clean.append({"name": name, "subject": str(t.get("subject") or "")[:200], "body": str(t.get("body") or "")[:20000],
                      "category": t.get("category") if t.get("category") in CATEGORIES else "service"})
    db.set_setting(TEMPLATES_KEY, json.dumps(clean))
    return jsonify(clean)


# ---------- sending ----------

def create_campaign(subject, body, category, audience, recipients, skipped):
    with db.transaction():
        row = db.execute(
            """INSERT INTO email_campaigns (subject, body, category, audience, recipients, skipped, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (subject, body, category, json.dumps(audience), len(recipients), sum(skipped.values()), session["user_id"]),
            returning=True, commit=False)
        db.execute_values(
            """INSERT INTO email_log (user_id, to_address, template, category, subject, campaign_id, sent_by)
               VALUES %s""",
            [(r["id"], r["email"], "campaign", "campaign", subject[:300], row["id"], session["user_id"]) for r in recipients],
            commit=False)
    return row["id"]


def start(campaign_id):
    app = current_app._get_current_object()

    def runner():
        with app.app_context():
            try:
                run(campaign_id)
            except Exception as e:
                log.exception("campaign %s failed", campaign_id)
                import errors
                errors.record("job", e, location="campaign")
            finally:
                db.close_db()

    return _POOL.submit(runner)


CLAIM = """
UPDATE email_log SET status = 'sending', sent_at = now()
 WHERE id = (SELECT id FROM email_log WHERE campaign_id = %s AND status = 'queued' ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
RETURNING id, user_id, to_address
"""


def run(campaign_id, sleep=time.sleep):
    campaign = db.query("SELECT * FROM email_campaigns WHERE id = %s", (campaign_id,), one=True)
    if not campaign or campaign["status"] != "sending":
        return 0
    sent = 0
    while True:
        if (db.query("SELECT status FROM email_campaigns WHERE id = %s", (campaign_id,), one=True) or {}).get("status") != "sending":
            break
        row = db.execute(CLAIM, (campaign_id,), returning=True)
        if not row:
            break
        person = db.query("""SELECT u.id, u.username, u.email, s.trial_end FROM users u
                              LEFT JOIN subscriptions s ON s.user_id = u.id WHERE u.id = %s""", (row["user_id"],), one=True)
        if not person:
            db.execute("UPDATE email_log SET status = 'skipped', error = 'account deleted' WHERE id = %s", (row["id"],))
            continue
        subject, text, html = render_for(person, campaign["subject"], campaign["body"], campaign["category"])
        headers = {}
        if campaign["category"] == "marketing":
            link = f"{_base()}/api/public/unsubscribe?t={unsubscribe_token(person['id'])}"
            headers = {"List-Unsubscribe": f"<{link}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        ok, err, message_id = mailer._deliver(row["to_address"], subject, text, html, headers=headers)
        mailer._log_result(row["id"], subject, ok, err, message_id)
        sent += 1
        sleep(SEND_INTERVAL)
    finish(campaign_id)
    return sent


def finish(campaign_id):
    db.execute(
        """UPDATE email_campaigns c SET
              sent = (SELECT COUNT(*) FROM email_log WHERE campaign_id = c.id AND status = 'sent'),
              failed = (SELECT COUNT(*) FROM email_log WHERE campaign_id = c.id AND status = 'failed'),
              status = CASE WHEN c.status = 'sending'
                             AND NOT EXISTS (SELECT 1 FROM email_log WHERE campaign_id = c.id AND status IN ('queued', 'sending'))
                            THEN 'done' ELSE c.status END,
              finished_at = CASE WHEN c.status = 'sending' THEN now() ELSE c.finished_at END
            WHERE id = %s""", (campaign_id,))


def resume_stalled():
    """From the hourly timer: a worker that died mid-campaign leaves rows queued, or claimed and
    never finished. The latter are retried — at worst one person gets that one message twice."""
    db.execute(f"""UPDATE email_log SET status = 'queued', sent_at = NULL
                    WHERE status = 'sending' AND sent_at < now() - interval '{STALE_MINUTES} minutes'""")
    rows = db.query("""SELECT DISTINCT c.id FROM email_campaigns c JOIN email_log l ON l.campaign_id = c.id
                        WHERE c.status = 'sending' AND l.status = 'queued'
                          AND c.created_at < now() - make_interval(mins => %s)""", (STALE_MINUTES,)) or []
    for r in rows:
        run(r["id"])
    return len(rows)
