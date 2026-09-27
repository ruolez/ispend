"""Housekeeping that has no request to ride on: keeping cached access states current and the
revenue ledger in step with Stripe.

Runs from the billing ticker's thread (see billing_tick) under its own hourly lease, and in its own
try block — billing_tick.tick() returns early when email or billing is off, and none of this may
depend on either.
"""

import logging
from datetime import datetime, timedelta, timezone

import db
import ledger

log = logging.getLogger(__name__)

LEASE_KEY = "ops_tick_at"
BACKFILL_KEY = "ledger_backfill_done"
RECONCILE_EVERY = timedelta(hours=24)
# Operational history: long enough to see a trend, short enough not to become a dossier.
PRUNE = (
    ("app_errors", "created_at", 30),
    ("login_events", "created_at", 365),
    ("email_log", "created_at", 365),
)
RECONCILE_OVERLAP = timedelta(days=2)


def _reconcile_due(now):
    """(due, since). The first run imports everything; after that, a daily pass over recent
    invoices, overlapping the last one so an invoice finalized late is not missed."""
    import billing_reconcile

    if db.get_setting(BACKFILL_KEY) != "1":
        return True, None
    last = billing_reconcile.last_result() or {}
    try:
        at = datetime.fromisoformat(last.get("started_at"))
    except (TypeError, ValueError):
        return True, None
    return now - at >= RECONCILE_EVERY, at - RECONCILE_OVERLAP


def prune():
    out = {}
    for table, column, days in PRUNE:
        out[table] = db.execute(
            f"DELETE FROM {table} WHERE {column} < now() - make_interval(days => %s)",  # noqa: S608 - fixed list
            (days,))
    return out


def tick(now=None):
    import billing
    import billing_tick

    if not billing_tick.claim(LEASE_KEY):
        return {"skipped": "another worker holds the lease"}
    now = now or datetime.now(timezone.utc)
    import activity
    import admin_metrics

    import privacy

    out = {"ent_states_changed": ledger.refresh_all_ent_states(), "pruned": prune(),
           "exports_expired": privacy.prune_exports(),
           "snapshot": admin_metrics.snapshot(activity.today())}
    if billing.enabled():
        import billing_reconcile

        due, since = _reconcile_due(now)
        if due and not billing_reconcile.is_running():
            result = billing_reconcile.run(since=since)
            out["reconcile"] = {k: result[k] for k in ("checked", "history_events", "payments_added", "error")}
            if not result.get("error"):
                db.set_setting(BACKFILL_KEY, "1")
    return out
