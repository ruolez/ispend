"""Bring the local revenue ledger in line with Stripe.

Webhooks are the normal path; this is the safety net for the ones that never arrived, and the
one-off import of history from before the ledger existed. It reads every subscription and invoice
Stripe has, fixes subscription rows that disagree, records invoices we never saw, and for
subscriptions whose early life predates the ledger it writes that history from Stripe's own
timestamps (started, trial, cancelled, ended).

Safe to run repeatedly: invoices upsert on their id, synthesized history is only written for the
stretch before a subscription's first recorded event, and a subscription row is only rewritten
when something differs.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import billing
import db
import ledger

log = logging.getLogger(__name__)

LAST_KEY = "billing_reconcile_last"
RUNNING_KEY = "billing_reconcile_running"
RUNNING_FOR = timedelta(hours=1)
ENDED = ("canceled", "incomplete_expired", "unpaid")


def _ts(value):
    return datetime.fromtimestamp(int(value), timezone.utc) if value else None


def is_running():
    raw = db.get_setting(RUNNING_KEY)
    if not raw:
        return False
    try:
        return datetime.fromisoformat(raw) > datetime.now(timezone.utc) - RUNNING_FOR
    except ValueError:
        return False


def last_result():
    try:
        return json.loads(db.get_setting(LAST_KEY) or "null")
    except ValueError:
        return None


def primary_subscription(subs, local_id=None):
    """The one subscription the user's row should mirror: the one it already points at, else the
    newest that has not ended, else the newest."""
    for sub in subs:
        if local_id and sub.get("id") == local_id:
            return sub
    live = [s for s in subs if s.get("status") not in ENDED]
    pool = live or subs
    return max(pool, key=lambda s: int(s.get("created") or 0))


def differences(local, sub):
    """Field names where our row disagrees with Stripe."""
    local = local or {}
    snap = ledger.snapshot(sub)
    pid = (((sub.get("items") or {}).get("data") or [{}])[0].get("price") or {}).get("id")
    theirs = {
        "stripe_subscription_id": sub.get("id"),
        "status": sub.get("status"),
        "plan": billing.plan_for_price(pid),
        "mrr_cents": snap["mrr_cents"],
        "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
        "current_period_end": billing._period_end(sub),
    }
    out = []
    for key, value in theirs.items():
        mine = local.get(key)
        if key == "cancel_at_period_end":
            mine = bool(mine)
        if key == "mrr_cents":
            mine = int(mine or 0)
        if mine != value:
            out.append(key)
    return out


def history_events(sub, before=None, now=None):
    """[(kind, mrr_from, mrr_to, occurred_at, status_to)] for one subscription's life as Stripe
    tells it, keeping only what happened before `before` (the first event we already hold)."""
    now = now or datetime.now(timezone.utc)
    snap = ledger.snapshot(sub, now=now)
    value = ledger.mrr_cents(snap, "active", now=now)
    status = sub.get("status")
    started = _ts(sub.get("start_date")) or _ts(sub.get("created"))
    trial_start, trial_end = _ts(sub.get("trial_start")), _ts(sub.get("trial_end"))
    ended = _ts(sub.get("ended_at")) or (_ts(sub.get("canceled_at")) if status in ENDED else None)
    paid_from = trial_end if trial_end and trial_start else started
    paid = paid_from is not None and (
        status in ledger.PAYING_STATUSES or (ended is not None and ended > paid_from))
    events = []
    if trial_start:
        events.append(("trial_started", 0, 0, trial_start, "trialing"))
    if paid:
        events.append(("subscribed", 0, value, paid_from, "active"))
    if ended:
        events.append(("canceled", value if paid else 0, 0, ended, status if status in ENDED else "canceled"))
    return [e for e in events if e[3] is not None and (before is None or e[3] < before)]


def backfill_history(uid, subs, now=None):
    """Write the pre-ledger history of each subscription. Returns how many events were written."""
    first_seen = {r["stripe_subscription_id"]: r["first_at"] for r in (db.query(
        """SELECT stripe_subscription_id, MIN(occurred_at) AS first_at FROM subscription_events
            WHERE user_id = %s AND stripe_subscription_id IS NOT NULL AND source <> 'system'
            GROUP BY 1""", (uid,)) or [])}
    written = 0
    for sub in subs:
        snap = ledger.snapshot(sub, now=now)
        for kind, m0, m1, at, status_to in history_events(sub, before=first_seen.get(sub.get("id")), now=now):
            written += db.execute(ledger.INSERT_EVENT, (
                uid, sub.get("id"), None, "reconcile", kind, None, status_to, None,
                None, m0, m1, snap.get("currency"), json.dumps({"from": "stripe_history"}), at)) or 0
    if written:
        # The migration's placeholder said "unknown, worth 0"; the real history replaces it.
        db.execute("DELETE FROM subscription_events WHERE user_id = %s AND kind = 'baseline'", (uid,))
    return written


def _invoice_status(inv):
    status = inv.get("status")
    if status == "paid":
        return "paid"
    if status == "void":
        return "void"
    if status == "uncollectible" or (status == "open" and int(inv.get("attempt_count") or 0) > 0):
        return "failed"
    return None


def run(since=None, actor_id=None):
    """One full pass. since: only invoices created after this (None = all of them)."""
    stripe = billing.client()
    started = datetime.now(timezone.utc)
    db.set_setting(RUNNING_KEY, started.isoformat())
    result = {"started_at": started.isoformat(), "by": actor_id, "checked": 0, "fixed": [],
              "history_events": 0, "payments_added": 0, "unknown_customers": 0, "error": None}
    try:
        by_user = {}
        for sub in stripe.Subscription.list(status="all", limit=100,
                                            expand=["data.discounts"]).auto_paging_iter():
            uid = billing.user_for_customer(sub.get("customer"))
            if uid is None:
                result["unknown_customers"] += 1
                continue
            by_user.setdefault(uid, []).append(sub)
        for uid, subs in by_user.items():
            result["checked"] += 1
            result["history_events"] += backfill_history(uid, subs)
            local = db.query(
                """SELECT stripe_subscription_id, status, plan, mrr_cents, cancel_at_period_end,
                          current_period_end FROM subscriptions WHERE user_id = %s""", (uid,), one=True)
            sub = primary_subscription(subs, (local or {}).get("stripe_subscription_id"))
            fields = differences(local, sub)
            if fields:
                billing.apply_subscription(uid, sub, event_created=0, source="reconcile")
                result["fixed"].append({"user_id": uid, "fields": fields})
        params = {"limit": 100}
        if since:
            params["created"] = {"gte": int(since.timestamp())}
        for inv in stripe.Invoice.list(**params).auto_paging_iter():
            status = _invoice_status(inv)
            if not status:
                continue
            uid = billing.user_for_customer(inv.get("customer"))
            if ledger.upsert_invoice(inv, uid, status, plan=billing._invoice_plan(inv)):
                result["payments_added"] += 1
    except Exception as e:
        log.exception("reconcile with Stripe failed")
        result["error"] = str(e)[:300]
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        db.set_setting(LAST_KEY, json.dumps(result))
        db.set_setting(RUNNING_KEY, "")
    return result
