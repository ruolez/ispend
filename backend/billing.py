"""Stripe: customers, Checkout, the Billing Portal, and applying webhook state.

Hosted Checkout and Portal rather than embedded Elements: card data never touches this server
(SAQ-A rather than SAQ-A-EP), and a full-page redirect needs no CSP change, whereas the app sets
frame-ancestors 'none' and X-Frame-Options: DENY.
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from flask import request

import config
import db

log = logging.getLogger(__name__)

API_VERSION = "2026-08-26.dahlia"
PLANS = ("monthly", "yearly")
_prices_cache = {"at": 0, "data": None}
PRICE_TTL = 600


class BillingError(Exception):
    """User-facing; the message is shown to the admin or the user verbatim."""


def _setting(key, env_value):
    """Env wins: a self-hoster keeps the secret in a chmod 600 .env that is never in the database,
    while the operator can still rotate a price id from the admin UI without a redeploy."""
    if env_value:
        return env_value
    return db.get_setting(key) or ""


def secret_key():
    return _setting("stripe_secret_key", config.STRIPE_SECRET_KEY)


def webhook_secret():
    return _setting("stripe_webhook_secret", config.STRIPE_WEBHOOK_SECRET)


def price_id(plan):
    env = {"monthly": config.STRIPE_PRICE_MONTHLY, "yearly": config.STRIPE_PRICE_YEARLY}.get(plan, "")
    return _setting(f"stripe_price_{plan}", env) or None


def plan_for_price(pid):
    for plan in PLANS:
        if pid and price_id(plan) == pid:
            return plan
    return None


def env_locked(key):
    return bool({"stripe_secret_key": config.STRIPE_SECRET_KEY,
                 "stripe_webhook_secret": config.STRIPE_WEBHOOK_SECRET,
                 "stripe_price_monthly": config.STRIPE_PRICE_MONTHLY,
                 "stripe_price_yearly": config.STRIPE_PRICE_YEARLY}.get(key))


def enabled():
    """Billing off => entitlement.evaluate() returns active for everyone, so an existing
    self-hosted install upgrades into exactly the behaviour it had before."""
    try:
        return bool(secret_key()) and bool(price_id("monthly"))
    except Exception:       # no app/DB context (import time, a job starting up)
        return False


def base_url():
    """Stripe redirects back here. request.url_root reports http:// behind the TLS terminator
    because create_app installs no ProxyFix, so an HTTPS install must set APP_BASE_URL."""
    if config.APP_BASE_URL:
        return config.APP_BASE_URL.rstrip("/")
    if config.SESSION_COOKIE_SECURE:
        raise BillingError("APP_BASE_URL must be set when iSpend is served over HTTPS.")
    return request.url_root.rstrip("/")


def client():
    import stripe
    key = secret_key()
    if not key:
        raise BillingError("Stripe is not configured on this server.")
    stripe.api_key = key
    stripe.api_version = API_VERSION
    stripe.max_network_retries = 2
    return stripe


def prices():
    """[{plan, price_id, amount_cents, currency, interval}] for the plan picker, cached briefly."""
    now = time.time()
    if _prices_cache["data"] is not None and now - _prices_cache["at"] < PRICE_TTL:
        return _prices_cache["data"]
    stripe = client()
    out = []
    for plan in PLANS:
        pid = price_id(plan)
        if not pid:
            continue
        try:
            p = stripe.Price.retrieve(pid)
        except Exception as e:
            log.warning("could not read Stripe price %s: %s", pid, e)
            continue
        recurring = p.get("recurring") or {}
        out.append({"plan": plan, "price_id": pid, "amount_cents": p.get("unit_amount"),
                    "currency": (p.get("currency") or "usd").upper(),
                    "interval": recurring.get("interval")})
    _prices_cache.update({"at": now, "data": out})
    return out


def _ts(value):
    return datetime.fromtimestamp(value, timezone.utc) if value else None


def _period_end(sub):
    """Recent Stripe API versions moved current_period_end from the subscription onto the
    subscription item. Read both: a silent None here reads as "active forever" downstream."""
    items = ((sub.get("items") or {}).get("data") or [])
    if items and items[0].get("current_period_end"):
        return _ts(items[0]["current_period_end"])
    return _ts(sub.get("current_period_end"))


def ensure_customer(row):
    """Create the Stripe customer BEFORE the redirect, not on the webhook.

    With the customer -> user mapping already in our database, every later event resolves by
    stripe_customer_id and webhook ordering stops mattering. Waiting for
    checkout.session.completed to learn the id means a subscription.created that arrives first
    is an orphan that has to be queued or dropped."""
    if row.get("stripe_customer_id"):
        return row["stripe_customer_id"]
    stripe = client()
    customer = stripe.Customer.create(
        email=row.get("email") or None,
        name=row.get("username"),
        metadata={"ispend_user_id": str(row["id"])},
        idempotency_key=f"ispend-cust-{row['id']}",
    )
    db.execute(
        """INSERT INTO subscriptions (user_id, status, stripe_customer_id)
           VALUES (%s, 'none', %s)
           ON CONFLICT (user_id) DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id,
                                               updated_at = now()""",
        (row["id"], customer["id"]))
    return customer["id"]


def create_checkout_session(row, plan):
    stripe = client()
    pid = price_id(plan)
    if not pid:
        raise BillingError("That plan is not available on this server.")
    customer_id = ensure_customer(row)
    data = {"metadata": {"ispend_user_id": str(row["id"])}}
    trial_end = row.get("trial_end")
    now = datetime.now(timezone.utc)
    # Honour the remaining local trial so someone subscribing on day 3 of 14 is not charged
    # immediately. Stripe requires trial_end >= now + 48h; below that, just charge now.
    if trial_end and trial_end > now + timedelta(hours=48):
        data["trial_end"] = int(trial_end.timestamp())
    session_obj = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        client_reference_id=str(row["id"]),
        line_items=[{"price": pid, "quantity": 1}],
        allow_promotion_codes=True,
        billing_address_collection="auto",
        subscription_data=data,
        success_url=f"{base_url()}/billing.html?checkout=success",
        cancel_url=f"{base_url()}/billing.html?checkout=cancel",
    )
    return session_obj["url"]


def create_portal_session(row):
    stripe = client()
    customer_id = row.get("stripe_customer_id")
    if not customer_id:
        raise BillingError("There is no subscription to manage yet.")
    session_obj = stripe.billing_portal.Session.create(
        customer=customer_id, return_url=f"{base_url()}/billing.html")
    return session_obj["url"]


APPLY_SQL = """
UPDATE subscriptions SET
  status = %(status)s,
  price_id = %(price_id)s,
  plan = %(plan)s,
  stripe_subscription_id = %(sub_id)s,
  trial_end = %(trial_end)s,
  current_period_end = %(cpe)s,
  cancel_at_period_end = %(cape)s,
  lapsed_at = CASE WHEN %(good)s THEN NULL ELSE COALESCE(lapsed_at, %(anchor)s) END,
  grace_until = CASE WHEN %(good)s THEN NULL
                     ELSE COALESCE(grace_until, %(anchor)s + make_interval(days => %(grace)s)) END,
  payment_failed_email_at = CASE WHEN %(good)s THEN NULL ELSE payment_failed_email_at END,
  grace_ending_email_at   = CASE WHEN %(good)s THEN NULL ELSE grace_ending_email_at END,
  read_only_email_at      = CASE WHEN %(good)s THEN NULL ELSE read_only_email_at END,
  trial_ended_email_at    = CASE WHEN %(good)s THEN NULL ELSE trial_ended_email_at END,
  last_event_at = GREATEST(last_event_at, %(event_created)s),
  synced_at = now(), updated_at = now()
WHERE user_id = %(uid)s AND (%(event_created)s = 0 OR %(event_created)s >= last_event_at)
"""


def apply_subscription(user_id, sub, event_created=0):
    """Write one Stripe subscription object onto our row. Out-of-order deliveries are dropped by
    the WHERE clause; a manual refresh passes 0 to force itself through."""
    import entitlement

    status = sub.get("status") or "none"
    item = ((sub.get("items") or {}).get("data") or [{}])[0]
    pid = ((item.get("price") or {}).get("id")) or None
    period_end = _period_end(sub)
    good = status in entitlement.GOOD_STATUSES
    anchor = period_end or datetime.now(timezone.utc)
    return db.execute(APPLY_SQL, {
        "status": status, "price_id": pid, "plan": plan_for_price(pid),
        "sub_id": sub.get("id"), "trial_end": _ts(sub.get("trial_end")), "cpe": period_end,
        "cape": bool(sub.get("cancel_at_period_end")), "good": good, "anchor": anchor,
        "grace": entitlement.grace_days(), "event_created": int(event_created or 0),
        "uid": user_id,
    })


def user_for_customer(customer_id):
    row = db.query("SELECT user_id FROM subscriptions WHERE stripe_customer_id = %s",
                   (customer_id,), one=True)
    return row["user_id"] if row else None


def refresh_from_stripe(user_id):
    """Lazy reconciliation for a webhook we never received."""
    row = db.query("SELECT stripe_subscription_id, stripe_customer_id FROM subscriptions "
                   "WHERE user_id = %s", (user_id,), one=True)
    if not row or not row.get("stripe_subscription_id"):
        return None
    stripe = client()
    sub = stripe.Subscription.retrieve(row["stripe_subscription_id"])
    apply_subscription(user_id, sub, event_created=0)
    return sub.get("status")


# ---------- webhook ----------

HANDLED = {
    "checkout.session.completed",
    "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted",
    "invoice.payment_failed", "invoice.paid",
    "customer.subscription.trial_will_end",
}


class WebhookMismatch(Exception):
    """The event does not belong to the user its customer maps to."""


def handle_event(event):
    """Returns 'processed' or 'ignored'. Raises to make Stripe retry."""
    import mailer

    etype = event.get("type")
    if etype not in HANDLED:
        return "ignored", None
    obj = (event.get("data") or {}).get("object") or {}
    created = int(event.get("created") or 0)

    if etype == "checkout.session.completed":
        customer_id = obj.get("customer")
        uid = user_for_customer(customer_id)
        claimed = obj.get("client_reference_id")
        if uid is None or (claimed and str(uid) != str(claimed)):
            # Never trust the browser's claim: the customer was created server-side with the
            # user id in its metadata, and both must agree.
            raise WebhookMismatch(f"customer {customer_id} claims user {claimed}, maps to {uid}")
        sub_id = obj.get("subscription")
        if sub_id:
            sub = client().Subscription.retrieve(sub_id)
            apply_subscription(uid, sub, created)
            _notify(mailer, uid, "subscription_started")
        return "processed", uid

    if etype.startswith("customer.subscription."):
        uid = user_for_customer(obj.get("customer"))
        if uid is None:
            return "ignored", None
        apply_subscription(uid, obj, created)
        if etype == "customer.subscription.deleted":
            _notify(mailer, uid, "subscription_canceled")
        elif etype == "customer.subscription.trial_will_end":
            _stamped_notify(mailer, uid, "trial_ending", "trial_ending_email_at")
        return "processed", uid

    if etype == "invoice.payment_failed":
        uid = user_for_customer(obj.get("customer"))
        if uid is None:
            return "ignored", None
        import entitlement
        db.execute(
            """UPDATE subscriptions
                  SET lapsed_at = COALESCE(lapsed_at, now()),
                      grace_until = COALESCE(grace_until, now() + make_interval(days => %s)),
                      updated_at = now()
                WHERE user_id = %s""", (entitlement.grace_days(), uid))
        _stamped_notify(mailer, uid, "payment_failed", "payment_failed_email_at")
        return "processed", uid

    if etype == "invoice.paid":
        uid = user_for_customer(obj.get("customer"))
        if uid is None:
            return "ignored", None
        db.execute(
            """UPDATE subscriptions SET lapsed_at = NULL, grace_until = NULL,
                      payment_failed_email_at = NULL, grace_ending_email_at = NULL,
                      read_only_email_at = NULL, updated_at = now()
                WHERE user_id = %s""", (uid,))
        return "processed", uid

    return "ignored", None


def _recipient(uid):
    row = db.query("SELECT email, username FROM users WHERE id = %s", (uid,), one=True)
    return row if row and row.get("email") else None


def _notify(mailer, uid, template, **ctx):
    row = _recipient(uid)
    if row:
        mailer.send_async(template, row["email"], username=row["username"], **ctx)


def _stamped_notify(mailer, uid, template, column):
    """Send once per lapse; the stamp is cleared when the account recovers."""
    n = db.execute(f"UPDATE subscriptions SET {column} = now() WHERE user_id = %s AND {column} IS NULL",
                   (uid,))
    if n:
        _notify(mailer, uid, template)
