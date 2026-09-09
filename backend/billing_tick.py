"""Lifecycle emails, which have no user request to ride on.

There is no scheduler in this app and adding one would mean a fourth container. Instead a thread
per worker wakes periodically and claims an hourly lease, so N workers across N replicas do the
work exactly once.

The thread MUST be started lazily from a request: gunicorn runs create_app() in the master before
forking under --preload, so a thread started there dies with the master and never runs.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import db
import entitlement
import mailer

log = logging.getLogger(__name__)

INTERVAL_SECONDS = 900
LEASE_KEY = "billing_tick_at"
LEASE = "1 hour"
_started = threading.Event()

CANDIDATE_SQL = """
SELECT u.id, u.username, u.email, u.role, u.created_at,
       s.status AS sub_status, s.plan, s.trial_end, s.current_period_end,
       s.cancel_at_period_end, s.lapsed_at, s.grace_until, s.comped_until,
       s.stripe_subscription_id,
       s.trial_ending_email_at, s.trial_ended_email_at,
       s.grace_ending_email_at, s.read_only_email_at
  FROM users u JOIN subscriptions s ON s.user_id = u.id
 WHERE u.status = 'active' AND u.email IS NOT NULL AND u.role <> 'admin'
   AND (s.comped_until IS NULL OR s.comped_until < now())
"""


def _claim():
    """One atomic statement is the whole concurrency story: rowcount 1 means this worker won the
    hour. No new table, no advisory-lock lifetime to reason about, correct across replicas."""
    db.execute("INSERT INTO settings (key, value) VALUES (%s, '') ON CONFLICT (key) DO NOTHING",
               (LEASE_KEY,))
    n = db.execute(
        f"""UPDATE settings SET value = to_char(now(), 'YYYY-MM-DD"T"HH24:MI:SSOF'),
                                updated_at = now()
             WHERE key = %s
               AND (value IS NULL OR value = ''
                    OR value::timestamptz < now() - interval '{LEASE}')""",
        (LEASE_KEY,))
    return bool(n)


def _due(row, now):
    """Which one message this account is owed right now, if any."""
    ent = entitlement.evaluate(row, now=now)
    state, link = ent["state"], "/billing.html"
    trial_end, grace_until = row.get("trial_end"), ent.get("grace_until")

    if state == entitlement.TRIALING and trial_end and trial_end <= now + timedelta(days=3):
        if not row.get("trial_ending_email_at"):
            return ("trial_ending", "trial_ending_email_at",
                    {"trial_end": trial_end.strftime("%d %B %Y"), "link": link})
    if state == entitlement.GRACE:
        if trial_end and trial_end <= now and not row.get("trial_ended_email_at"):
            return ("trial_ended", "trial_ended_email_at",
                    {"days": ent.get("days_left"), "link": link})
        if grace_until and grace_until <= now + timedelta(days=2) and not row.get("grace_ending_email_at"):
            return ("grace_ending", "grace_ending_email_at",
                    {"until": grace_until.strftime("%d %B %Y"), "link": link})
    if state == entitlement.READ_ONLY and not row.get("read_only_email_at"):
        return ("read_only", "read_only_email_at", {"link": link})
    return None


def tick(base_url=""):
    """One pass. Never mutates entitlement state: the evaluator is purely time-based, so there is
    nothing to flip."""
    if not mailer.configured():
        return {"skipped": "smtp not configured"}
    import billing
    if not billing.enabled():
        return {"skipped": "billing not configured"}
    if not _claim():
        return {"skipped": "another worker holds the lease"}
    now = datetime.now(timezone.utc)
    sent = {}
    for row in db.query(CANDIDATE_SQL) or []:
        due = _due(row, now)
        if not due:
            continue
        template, column, ctx = due
        # Stamp first: a crash after sending must not re-send on the next tick.
        n = db.execute(f"UPDATE subscriptions SET {column} = now() WHERE user_id = %s "
                       f"AND {column} IS NULL", (row["id"],))
        if not n:
            continue
        ctx["link"] = f"{base_url}{ctx['link']}" if base_url else ctx["link"]
        mailer.send_template(template, row["email"], username=row["username"], **ctx)
        sent[template] = sent.get(template, 0) + 1
    return {"sent": sent}


def _loop(app):
    while True:
        time.sleep(INTERVAL_SECONDS)
        try:
            with app.app_context():
                import config
                tick(base_url=(config.APP_BASE_URL or "").rstrip("/"))
                db.close_db()
        except Exception:
            log.warning("billing tick failed", exc_info=True)


def start(app):
    """Idempotent, and called from a request so it runs in the worker rather than the preload
    master."""
    if _started.is_set():
        return
    _started.set()
    threading.Thread(target=_loop, args=(app,), daemon=True, name="billing-tick").start()
    log.info("billing ticker started")
