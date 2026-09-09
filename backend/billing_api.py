"""User-facing billing: plan status, Checkout, the Billing Portal, and the Stripe webhook."""

import logging

from flask import Blueprint, jsonify, request, session

import billing
import db
import entitlement
import jobs
from auth import login_required
from util import api_error, audit, json_body

log = logging.getLogger(__name__)

bp = Blueprint("billing", __name__, url_prefix="/api/billing")

STATUS_SQL = """
SELECT u.id, u.role, u.email, u.email_verified_at, u.username, u.created_at,
       s.status AS sub_status, s.plan, s.price_id, s.stripe_customer_id, s.stripe_subscription_id,
       s.trial_end, s.current_period_end, s.cancel_at_period_end, s.lapsed_at, s.grace_until,
       s.comped_until, s.synced_at
  FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id
 WHERE u.id = %s
"""


def _row(user_id=None):
    return db.query(STATUS_SQL, (user_id or session["user_id"],), one=True)


@bp.get("/status")
@login_required
def status():
    row = _row()
    ent = entitlement.evaluate(row)
    out = entitlement.public_json(ent)
    out["email_verified"] = bool(row.get("email_verified_at"))
    out["prices"] = []
    if billing.enabled():
        try:
            out["prices"] = billing.prices()
        except billing.BillingError as e:
            log.warning("could not list Stripe prices: %s", e)
    return jsonify(out)


@bp.post("/checkout")
@login_required
def checkout():
    if not billing.enabled():
        return api_error("Billing is not configured on this server.", 400)
    row = _row()
    ent = entitlement.evaluate(row)
    if ent["state"] == entitlement.ACTIVE and row.get("stripe_subscription_id"):
        return api_error("You already have an active subscription.", 409)
    # Verification is not required to use iSpend, but it is required to pay: this is what makes
    # the Stripe customer's email address real.
    if not row.get("email"):
        return jsonify({"error": "Add an email address before subscribing.",
                        "code": "email_missing"}), 403
    if not row.get("email_verified_at"):
        return jsonify({"error": "Confirm your email address before subscribing.",
                        "code": "email_unverified"}), 403
    plan = json_body().get("plan") or "monthly"
    if plan not in billing.PLANS:
        return api_error("Unknown plan")
    try:
        url = billing.create_checkout_session(row, plan)
    except billing.BillingError as e:
        return api_error(str(e))
    except Exception as e:
        log.exception("Stripe checkout failed")
        return api_error(f"Stripe could not start checkout: {e}", 502)
    audit("billing.checkout", {"plan": plan})
    return jsonify({"url": url})


@bp.post("/portal")
@login_required
def portal():
    if not billing.enabled():
        return api_error("Billing is not configured on this server.", 400)
    row = _row()
    try:
        url = billing.create_portal_session(row)
    except billing.BillingError as e:
        return api_error(str(e), 409)
    except Exception as e:
        log.exception("Stripe portal failed")
        return api_error(f"Stripe could not open the billing portal: {e}", 502)
    audit("billing.portal")
    return jsonify({"url": url})


@bp.post("/refresh")
@login_required
def refresh():
    """Why the success_url is never proof of payment: landing on it only triggers this, which
    re-reads the subscription from Stripe. Forging the URL grants nothing."""
    if not billing.enabled():
        return jsonify(entitlement.public_json(entitlement.evaluate(_row())))
    try:
        billing.refresh_from_stripe(session["user_id"])
    except Exception:
        log.warning("could not refresh subscription from Stripe", exc_info=True)
    return jsonify(entitlement.public_json(entitlement.evaluate(_row())))


@bp.post("/webhook")
def webhook():
    """No login_required and no json_body(): Stripe signs the exact request bytes, so the body
    must be read raw and must not have been parsed first."""
    if not billing.enabled() or not billing.webhook_secret():
        # An empty signing secret must never mean "accept anything".
        return api_error("Billing is not configured", 400)
    import stripe

    payload = request.get_data()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, billing.webhook_secret())
    except (ValueError, stripe.SignatureVerificationError):
        log.warning("rejected a Stripe webhook with a bad signature from %s", request.remote_addr)
        return api_error("Invalid signature", 400)

    try:
        with db.transaction():
            # The event row and the state change commit together: a crash leaves the event
            # unrecorded so Stripe's retry does the work instead of being swallowed.
            inserted = db.execute(
                """INSERT INTO stripe_events (id, type, stripe_created) VALUES (%s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (event["id"], event["type"], int(event.get("created") or 0)), commit=False)
            if not inserted:
                return jsonify({"received": True, "duplicate": True})
            outcome, uid = billing.handle_event(event)
            db.execute("UPDATE stripe_events SET status = %s, user_id = %s WHERE id = %s",
                       (outcome, uid, event["id"]), commit=False)
    except billing.WebhookMismatch as e:
        log.error("Stripe webhook identity mismatch: %s", e)
        audit("billing.webhook_mismatch", {"event": event.get("id"), "detail": str(e)[:300]},
              user_id=None)
        db.execute("""INSERT INTO stripe_events (id, type, stripe_created, status, error)
                      VALUES (%s, %s, %s, 'failed', %s)
                      ON CONFLICT (id) DO UPDATE SET status = 'failed', error = EXCLUDED.error""",
                   (event["id"], event["type"], int(event.get("created") or 0), str(e)[:500]))
        return api_error("Event does not match its customer", 400)
    _prune_events()
    return jsonify({"received": True})


def _prune_events():
    import random
    if random.random() < 0.01:  # noqa: S311 - sampling, not security
        db.execute("DELETE FROM stripe_events WHERE received_at < now() - interval '90 days'")


def refresh_after_login(user_id):
    """Spawned from auth.login when the local copy is stale, so login latency is untouched."""
    try:
        billing.refresh_from_stripe(user_id)
    except Exception:
        log.info("post-login subscription refresh failed", exc_info=True)


def maybe_refresh(row):
    if not billing.enabled() or not row.get("stripe_subscription_id"):
        return
    synced = row.get("synced_at")
    from datetime import datetime, timedelta, timezone
    if synced and synced > datetime.now(timezone.utc) - timedelta(hours=6):
        return
    jobs.spawn(refresh_after_login, row["id"])
