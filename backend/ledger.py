"""The revenue ledger: what each subscription was worth, and when that changed.

Everything the admin's revenue pages show is computed from two append-only tables —
subscription_events (status, plan and monthly value over time) and payments (invoices) — which
are written here, from webhooks, admin actions and the reconcile job. Stripe is never read to
draw a chart.

Stripe's object shapes move between API versions (prices moved from `plan` to `price`, discounts
from `discount` to `discounts`, invoice taxes from `tax` to `total_taxes`), so every reader below
tolerates both the old and the new place and treats anything missing as "unknown", not zero.
"""

import json
import logging
from datetime import datetime, timezone

import db

log = logging.getLogger(__name__)

PAYING_STATUSES = ("active", "past_due")
ENDED_STATUSES = ("canceled", "unpaid", "incomplete_expired")
# Monthly equivalents of one billing interval.
_PER_MONTH = {"month": 1.0, "year": 1 / 12, "week": 52 / 12, "day": 365 / 12}


def _ts(value):
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromtimestamp(int(value), timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _get(obj, *path):
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _first_item(sub):
    items = _get(sub, "items", "data") or []
    return items[0] if items and isinstance(items[0], dict) else {}


def _discounts(sub):
    """Expanded discount objects only. A bare id (the new API's default) cannot be read without
    another call; callers that care expand `discounts` first."""
    out = []
    for d in (sub.get("discounts") or []):
        if isinstance(d, dict):
            out.append(d)
    legacy = sub.get("discount")
    if isinstance(legacy, dict) and not out:
        out.append(legacy)
    return out


def unresolved_discounts(sub):
    return sum(1 for d in (sub.get("discounts") or []) if isinstance(d, str))


def _coupon(discount):
    return discount.get("coupon") or _get(discount, "source", "coupon") or {}


def snapshot(sub, now=None):
    """The price facts of one Stripe subscription, flattened for the subscriptions row."""
    now = now or datetime.now(timezone.utc)
    item = _first_item(sub)
    price = item.get("price") or item.get("plan") or {}
    if isinstance(price, str):
        price = {}
    recurring = price.get("recurring") or {}
    interval = recurring.get("interval") or price.get("interval")
    count = recurring.get("interval_count") or price.get("interval_count") or 1
    unit = price.get("unit_amount")
    if unit is None and price.get("amount") is not None:   # legacy Plan objects
        unit = price.get("amount")
    discount = None
    for d in _discounts(sub):
        coupon = _coupon(d)
        if not isinstance(coupon, dict):
            continue
        ends = _ts(d.get("end"))
        discount = {"coupon": coupon.get("id"), "percent_off": coupon.get("percent_off"),
                    "amount_off": coupon.get("amount_off"), "duration": coupon.get("duration"),
                    "ends_at": ends.isoformat() if ends else None}
        break
    snap = {
        "currency": (price.get("currency") or sub.get("currency") or "").lower() or None,
        "unit_amount_cents": int(unit) if unit is not None else None,
        "quantity": int(item.get("quantity") or sub.get("quantity") or 1),
        "billing_interval": interval,
        "interval_count": int(count),
        "discount": discount,
        "started_at": _ts(sub.get("start_date")),
        "canceled_at": _ts(sub.get("canceled_at")),
        "ended_at": _ts(sub.get("ended_at")),
    }
    snap["mrr_cents"] = mrr_cents(snap, sub.get("status"), now=now)
    return snap


def mrr_cents(snap, status, now=None):
    """Monthly recurring revenue of one subscription, in the price's minor unit.

    Stripe's definition: active and past_due count, trials do not; a yearly price counts as a
    twelfth; a recurring discount reduces it for as long as it lasts, a one-off discount does not."""
    now = now or datetime.now(timezone.utc)
    if status not in PAYING_STATUSES:
        return 0
    unit, interval = snap.get("unit_amount_cents"), snap.get("billing_interval")
    if unit is None or interval not in _PER_MONTH:
        return 0
    count = max(1, int(snap.get("interval_count") or 1))
    per_interval = unit * max(1, int(snap.get("quantity") or 1))
    discount = snap.get("discount") or {}
    ends = discount.get("ends_at")
    live = discount and discount.get("duration") != "once" and (
        not ends or datetime.fromisoformat(ends) > now)
    if live:
        if discount.get("percent_off"):
            per_interval *= max(0.0, 1 - float(discount["percent_off"]) / 100)
        elif discount.get("amount_off"):
            per_interval = max(0, per_interval - int(discount["amount_off"]))
    return int(round(per_interval * _PER_MONTH[interval] / count))


# ---------- subscription changes ----------

def classify(old, new):
    """[(kind, mrr_from, mrr_to)] for one change of a subscription row. Empty when nothing a
    revenue report cares about moved."""
    old = old or {}
    s0, s1 = old.get("status"), new.get("status")
    p0, p1 = old.get("plan"), new.get("plan")
    m0, m1 = int(old.get("mrr_cents") or 0), int(new.get("mrr_cents") or 0)
    out = []
    if m0 == 0 and m1 > 0:
        out.append(("subscribed", m0, m1))
    elif m0 > 0 and m1 == 0:
        out.append(("canceled" if s1 in ENDED_STATUSES else "status_changed", m0, m1))
    elif m0 > 0 and m1 > 0 and p0 and p1 and p0 != p1:
        out.append(("plan_changed", m0, m1))
    elif m0 != m1:
        out.append(("mrr_changed", m0, m1))
    elif s1 == "trialing" and s0 != "trialing":
        out.append(("trial_started", m0, m1))
    elif s0 != s1:
        out.append(("status_changed", m0, m1))
    elif p0 != p1:
        out.append(("plan_changed", m0, m1))
    c0, c1 = bool(old.get("cancel_at_period_end")), bool(new.get("cancel_at_period_end"))
    # Stripe clears the flag when the subscription actually ends; that is the cancellation itself,
    # not a change of mind.
    if c0 != c1 and s1 not in ENDED_STATUSES:
        out.append(("cancel_scheduled" if c1 else "cancel_unscheduled", m1, m1))
    return out


INSERT_EVENT = """
INSERT INTO subscription_events
  (user_id, stripe_subscription_id, stripe_event_id, source, kind, status_from, status_to,
   plan_from, plan_to, mrr_from_cents, mrr_to_cents, currency, detail, occurred_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


def record_change(uid, old, new, source, stripe_event_id=None, occurred_at=None, sub_id=None):
    """Append the typed events for one old -> new change of a user's subscription."""
    events = classify(old, new)
    at = occurred_at or datetime.now(timezone.utc)
    for kind, m0, m1 in events:
        db.execute(INSERT_EVENT, (
            uid, sub_id or new.get("stripe_subscription_id"), stripe_event_id, source, kind,
            (old or {}).get("status"), new.get("status"), (old or {}).get("plan"), new.get("plan"),
            m0, m1, new.get("currency"), None, at))
    return [k for k, _, _ in events]


def record_admin(uid, kind, detail=None):
    """An access change made by hand (comp, trial or grace extension, signup). The monthly value
    does not move — only Stripe charges money — but the history should say it happened."""
    row = db.query("SELECT status, plan, mrr_cents, currency, stripe_subscription_id "
                   "FROM subscriptions WHERE user_id = %s", (uid,), one=True) or {}
    mrr = int(row.get("mrr_cents") or 0)
    source = "signup" if kind == "trial_started" and (detail or {}).get("signup") else "admin"
    db.execute(INSERT_EVENT, (
        uid, row.get("stripe_subscription_id"), None, source, kind, row.get("status"),
        row.get("status"), row.get("plan"), row.get("plan"), mrr, mrr, row.get("currency"),
        json.dumps(detail, default=str) if detail else None, datetime.now(timezone.utc)))


def refresh_ent_state(uid):
    """Re-evaluate one user's access and cache it on the subscriptions row."""
    import entitlement

    row = db.query(
        """SELECT u.id, u.role, u.created_at, s.status AS sub_status, s.plan,
                  s.stripe_subscription_id, s.trial_end, s.current_period_end,
                  s.cancel_at_period_end, s.lapsed_at, s.grace_until, s.comped_until
             FROM users u JOIN subscriptions s ON s.user_id = u.id WHERE u.id = %s""",
        (uid,), one=True)
    if not row:
        return None
    state = entitlement.evaluate(row)["state"]
    db.execute("UPDATE subscriptions SET ent_state = %s, ent_state_at = now() WHERE user_id = %s",
               (state, uid))
    return state


def refresh_all_ent_states():
    """Hourly: the evaluator is time-based (a trial simply ends), so cached states go stale
    without any write happening. Only rows whose answer changed are written."""
    import entitlement

    rows = db.query(
        """SELECT u.id, u.role, u.created_at, s.status AS sub_status, s.plan,
                  s.stripe_subscription_id, s.trial_end, s.current_period_end,
                  s.cancel_at_period_end, s.lapsed_at, s.grace_until, s.comped_until,
                  s.ent_state
             FROM users u JOIN subscriptions s ON s.user_id = u.id""") or []
    changed = []
    for r in rows:
        state = entitlement.evaluate(r)["state"]
        if state != r.get("ent_state"):
            changed.append((state, r["id"]))
    for state, uid in changed:
        db.execute("UPDATE subscriptions SET ent_state = %s, ent_state_at = now() WHERE user_id = %s",
                   (state, uid))
    return len(changed)


# ---------- invoices ----------

def _sum_amounts(items):
    return sum(int(i.get("amount") or 0) for i in (items or []) if isinstance(i, dict))


def invoice_facts(inv):
    """The columns of a payments row, from a Stripe invoice of any recent API version."""
    line = (_get(inv, "lines", "data") or [{}])[0] or {}
    price = line.get("price") or line.get("plan") or _get(line, "pricing", "price_details") or {}
    if isinstance(price, str):
        price = {}
    recurring = price.get("recurring") or {}
    payment = ((_get(inv, "payments", "data") or [{}])[0] or {}).get("payment") or {}
    tax = inv.get("tax")
    if tax is None:
        tax = _sum_amounts(inv.get("total_taxes"))
    discounts = inv.get("total_discount_amounts")
    coupon = None
    for d in (inv.get("discounts") or []):
        if isinstance(d, dict):
            coupon = (_coupon(d) or {}).get("id")
            break
    status_transitions = inv.get("status_transitions") or {}
    return {
        "stripe_invoice_id": inv.get("id"),
        "stripe_customer_id": inv.get("customer"),
        "stripe_subscription_id": inv.get("subscription") or _get(inv, "parent", "subscription_details",
                                                                   "subscription"),
        "stripe_payment_intent_id": inv.get("payment_intent") if isinstance(inv.get("payment_intent"), str)
        else payment.get("payment_intent"),
        "stripe_charge_id": inv.get("charge") if isinstance(inv.get("charge"), str) else payment.get("charge"),
        "billing_reason": inv.get("billing_reason"),
        "currency": (inv.get("currency") or "usd").lower(),
        "subtotal_cents": int(inv.get("subtotal") or 0),
        "discount_cents": _sum_amounts(discounts),
        "tax_cents": int(tax or 0),
        "amount_paid_cents": int(inv.get("amount_paid") or 0),
        "billing_interval": recurring.get("interval") or price.get("interval"),
        "coupon": coupon,
        "period_start": _ts(line.get("period", {}).get("start") if isinstance(line.get("period"), dict) else None)
        or _ts(inv.get("period_start")),
        "period_end": _ts(line.get("period", {}).get("end") if isinstance(line.get("period"), dict) else None)
        or _ts(inv.get("period_end")),
        "attempt_count": inv.get("attempt_count"),
        "price_id": price.get("id"),
        "paid_at": _ts(status_transitions.get("paid_at")),
    }


UPSERT_PAYMENT = """
INSERT INTO payments
  (user_id, stripe_invoice_id, stripe_customer_id, stripe_subscription_id, stripe_payment_intent_id,
   stripe_charge_id, status, billing_reason, currency, subtotal_cents, discount_cents, tax_cents,
   amount_paid_cents, plan, billing_interval, coupon, period_start, period_end, attempt_count,
   paid_at, failed_at)
VALUES (%(user_id)s, %(stripe_invoice_id)s, %(stripe_customer_id)s, %(stripe_subscription_id)s,
        %(stripe_payment_intent_id)s, %(stripe_charge_id)s, %(status)s, %(billing_reason)s,
        %(currency)s, %(subtotal_cents)s, %(discount_cents)s, %(tax_cents)s, %(amount_paid_cents)s,
        %(plan)s, %(billing_interval)s, %(coupon)s, %(period_start)s, %(period_end)s,
        %(attempt_count)s, %(paid_at)s, %(failed_at)s)
ON CONFLICT (stripe_invoice_id) DO UPDATE SET
  status = CASE WHEN payments.status IN ('refunded', 'partially_refunded') AND EXCLUDED.status = 'paid'
                THEN payments.status ELSE EXCLUDED.status END,
  user_id = COALESCE(EXCLUDED.user_id, payments.user_id),
  stripe_payment_intent_id = COALESCE(EXCLUDED.stripe_payment_intent_id, payments.stripe_payment_intent_id),
  stripe_charge_id = COALESCE(EXCLUDED.stripe_charge_id, payments.stripe_charge_id),
  subtotal_cents = EXCLUDED.subtotal_cents, discount_cents = EXCLUDED.discount_cents,
  tax_cents = EXCLUDED.tax_cents, amount_paid_cents = EXCLUDED.amount_paid_cents,
  plan = COALESCE(EXCLUDED.plan, payments.plan),
  attempt_count = EXCLUDED.attempt_count,
  paid_at = COALESCE(EXCLUDED.paid_at, payments.paid_at),
  failed_at = COALESCE(payments.failed_at, EXCLUDED.failed_at),
  updated_at = now()
RETURNING (xmax = 0) AS inserted
"""


def upsert_invoice(inv, uid, status, plan=None):
    """status: 'paid' | 'failed' | 'void'. A failed invoice that is later paid is the same row;
    a refund recorded earlier is never overwritten back to 'paid' by a late event."""
    facts = invoice_facts(inv)
    if not facts["stripe_invoice_id"]:
        return None
    now = datetime.now(timezone.utc)
    facts.update({
        "user_id": uid, "status": status, "plan": plan,
        "paid_at": facts["paid_at"] or (now if status == "paid" else None),
        "failed_at": now if status == "failed" else None,
    })
    facts.pop("price_id", None)
    row = db.execute(UPSERT_PAYMENT, facts, returning=True)
    return bool(row and row.get("inserted"))


def record_refund(charge):
    """charge.refunded: find the payment by charge, then payment intent, then (older API
    versions) the invoice id on the charge."""
    refunded = int(charge.get("amount_refunded") or 0)
    status = "refunded" if refunded >= int(charge.get("amount_captured") or charge.get("amount") or 0) \
        else "partially_refunded"
    keys = [("stripe_charge_id", charge.get("id")),
            ("stripe_payment_intent_id", charge.get("payment_intent")),
            ("stripe_invoice_id", charge.get("invoice"))]
    for column, value in keys:
        if not isinstance(value, str) or not value:
            continue
        row = db.execute(
            f"""UPDATE payments SET amount_refunded_cents = %s, status = %s, updated_at = now()
                 WHERE {column} = %s RETURNING user_id""",   # noqa: S608 - column from the fixed list above
            (refunded, status, value), returning=True)
        if row:
            return row.get("user_id")
    return None
