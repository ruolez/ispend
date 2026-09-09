"""Admin-side billing: per-user overrides, the instance summary, and Stripe/SMTP configuration."""

import logging

from flask import Blueprint, jsonify, session

import billing
import db
import entitlement
import jobs
import mailer
from auth import admin_required
from settings_api import MASK
from util import api_error, audit, json_body, rows_json, to_int

log = logging.getLogger(__name__)

bp = Blueprint("admin_billing", __name__, url_prefix="/api/admin/billing")

SECRET_KEYS = {"stripe_secret_key", "stripe_webhook_secret", "smtp_password"}
STRIPE_KEYS = ["stripe_secret_key", "stripe_webhook_secret", "stripe_price_monthly", "stripe_price_yearly"]
NUMERIC_KEYS = {"billing_trial_days": (1, 90), "billing_grace_days": (0, 90)}

USER_SQL = """
SELECT u.id, u.role, u.created_at,
       s.status AS sub_status, s.plan, s.price_id, s.stripe_customer_id, s.stripe_subscription_id,
       s.trial_end, s.current_period_end, s.cancel_at_period_end, s.lapsed_at, s.grace_until,
       s.comped_until, s.synced_at
  FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id
 WHERE u.id = %s
"""


def _block(row):
    """The shape the admin Users table renders. state is never null: the row is created at signup,
    backfilled by migration 012 and self-healed by the evaluator."""
    ent = entitlement.evaluate(row)
    out = entitlement.public_json(ent)
    out["price_id"] = row.get("price_id")
    out["comped_until"] = ent.get("comped") and "forever" or None
    out["stripe_customer_id"] = row.get("stripe_customer_id")
    out["stripe_subscription_id"] = row.get("stripe_subscription_id")
    out["stripe_customer_url"] = (f"https://dashboard.stripe.com/customers/{row['stripe_customer_id']}"
                                  if row.get("stripe_customer_id") else None)
    out["synced_at"] = row.get("synced_at").isoformat() if row.get("synced_at") else None
    return out


def users_block(user_ids):
    """Called by admin_api for the users list; degrades to {} rather than breaking the page."""
    if not user_ids:
        return {}
    rows = db.query(
        """SELECT u.id, u.role, u.created_at, s.status AS sub_status, s.plan, s.price_id,
                  s.stripe_customer_id, s.stripe_subscription_id, s.trial_end,
                  s.current_period_end, s.cancel_at_period_end, s.lapsed_at, s.grace_until,
                  s.comped_until, s.synced_at
             FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id
            WHERE u.id = ANY(%s)""", (list(user_ids),)) or []
    return {r["id"]: _block(r) for r in rows}


def _load(user_id):
    return db.query(USER_SQL, (user_id,), one=True)


def _ensure_row(user_id):
    db.execute("INSERT INTO subscriptions (user_id, status) VALUES (%s, 'none') "
               "ON CONFLICT (user_id) DO NOTHING", (user_id,))


@bp.post("/users/<int:user_id>/comp")
@admin_required
def comp(user_id):
    if not _load(user_id):
        return api_error("User not found", 404)
    data = json_body()
    _ensure_row(user_id)
    if data.get("forever"):
        value, label = "infinity", "forever"
    elif data.get("until"):
        value, label = data["until"], str(data["until"])
    else:
        value, label = None, "cleared"
    db.execute("UPDATE subscriptions SET comped_until = %s::timestamptz, updated_at = now() "
               "WHERE user_id = %s", (value, user_id))
    audit("billing.comp", {"id": user_id, "until": label})
    return jsonify({"ok": True, "billing": _block(_load(user_id))})


@bp.post("/users/<int:user_id>/extend-trial")
@admin_required
def extend_trial(user_id):
    days = to_int(json_body().get("days"), "days", lo=1, hi=365, required=True)
    if not _load(user_id):
        return api_error("User not found", 404)
    _ensure_row(user_id)
    db.execute(
        """UPDATE subscriptions
              SET trial_end = GREATEST(COALESCE(trial_end, now()), now()) + make_interval(days => %s),
                  status = CASE WHEN status IN ('none','trialing','comped') THEN 'trialing' ELSE status END,
                  lapsed_at = NULL, grace_until = NULL,
                  trial_ending_email_at = NULL, trial_ended_email_at = NULL, updated_at = now()
            WHERE user_id = %s""", (days, user_id))
    audit("billing.extend_trial", {"id": user_id, "days": days})
    return jsonify({"ok": True, "billing": _block(_load(user_id))})


@bp.post("/users/<int:user_id>/extend-grace")
@admin_required
def extend_grace(user_id):
    days = to_int(json_body().get("days"), "days", lo=1, hi=365, required=True)
    if not _load(user_id):
        return api_error("User not found", 404)
    _ensure_row(user_id)
    db.execute(
        """UPDATE subscriptions
              SET grace_until = GREATEST(COALESCE(grace_until, now()), now()) + make_interval(days => %s),
                  grace_ending_email_at = NULL, read_only_email_at = NULL, updated_at = now()
            WHERE user_id = %s""", (days, user_id))
    audit("billing.extend_grace", {"id": user_id, "days": days})
    return jsonify({"ok": True, "billing": _block(_load(user_id))})


@bp.post("/users/<int:user_id>/cancel")
@admin_required
def cancel(user_id):
    row = _load(user_id)
    if not row:
        return api_error("User not found", 404)
    if not row.get("stripe_subscription_id"):
        return jsonify({"error": "That user has no Stripe subscription.",
                        "code": "no_subscription"}), 409
    immediately = bool(json_body().get("immediately"))
    try:
        stripe = billing.client()
        if immediately:
            stripe.Subscription.cancel(row["stripe_subscription_id"])
        else:
            stripe.Subscription.modify(row["stripe_subscription_id"], cancel_at_period_end=True)
        billing.refresh_from_stripe(user_id)
    except billing.BillingError as e:
        return api_error(str(e))
    except Exception as e:
        log.exception("Stripe cancel failed")
        return api_error(f"Stripe refused the cancellation: {e}", 502)
    audit("billing.cancel", {"id": user_id, "immediately": immediately})
    return jsonify({"ok": True, "billing": _block(_load(user_id))})


@bp.post("/users/<int:user_id>/sync")
@admin_required
def sync(user_id):
    if not _load(user_id):
        return api_error("User not found", 404)
    try:
        billing.refresh_from_stripe(user_id)
    except Exception as e:
        return api_error(f"Stripe sync failed: {e}", 502)
    return jsonify({"ok": True, "billing": _block(_load(user_id))})


@bp.post("/sync-stale")
@admin_required
def sync_stale():
    hours = to_int(json_body().get("older_than_hours"), "older_than_hours", lo=1, hi=8760) or 24
    rows = db.query(
        """SELECT user_id FROM subscriptions
            WHERE stripe_subscription_id IS NOT NULL
              AND (synced_at IS NULL OR synced_at < now() - make_interval(hours => %s))""",
        (hours,)) or []
    for row in rows:
        jobs.spawn(_sync_one, row["user_id"])
    audit("billing.sync_stale", {"queued": len(rows)})
    return jsonify({"queued": len(rows)})


def _sync_one(user_id):
    try:
        billing.refresh_from_stripe(user_id)
    except Exception:
        log.info("bulk sync failed for user %s", user_id, exc_info=True)


@bp.get("/summary")
@admin_required
def summary():
    rows = db.query(
        """SELECT u.id, u.role, u.created_at, s.status AS sub_status, s.plan, s.trial_end,
                  s.current_period_end, s.cancel_at_period_end, s.lapsed_at, s.grace_until,
                  s.comped_until, s.stripe_subscription_id
             FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id
            WHERE u.status <> 'deleted'""") or []
    counts = {}
    for row in rows:
        state = entitlement.evaluate(row)["state"]
        counts[state] = counts.get(state, 0) + 1
    email = db.query(
        """SELECT COUNT(*) FILTER (WHERE detail->>'ok' = 'true') AS sent_24h,
                  COUNT(*) FILTER (WHERE detail->>'ok' = 'false') AS failed_24h
             FROM audit_log WHERE action = 'email.send'
              AND created_at > now() - interval '24 hours'""", one=True) or {}
    hook = db.query(
        """SELECT MAX(received_at) AS last_event_at,
                  COUNT(*) FILTER (WHERE received_at > now() - interval '24 hours') AS events_24h,
                  COUNT(*) FILTER (WHERE status = 'failed'
                                    AND received_at > now() - interval '24 hours') AS failed_24h
             FROM stripe_events""", one=True) or {}
    trials = db.query(
        """SELECT COUNT(*) AS n FROM subscriptions
            WHERE trial_end BETWEEN now() AND now() + interval '7 days'""", one=True) or {"n": 0}
    return jsonify({
        "enabled": billing.enabled(),
        "signup_enabled": db.get_setting("signup_enabled") == "1",
        "trial_days": entitlement.trial_days(),
        "grace_days": entitlement.grace_days(),
        "counts": counts,
        "trials_ending_7d": trials["n"],
        "email": {"configured": mailer.configured(), **rows_json([email])[0]},
        "webhook": rows_json([hook])[0],
    })


@bp.get("/config")
@admin_required
def get_config():
    """Secrets come back masked, and a field set in .env is reported locked so the UI never
    round-trips a value it cannot change."""
    out = {}
    for key in STRIPE_KEYS:
        value = db.get_setting(key) or ""
        locked = billing.env_locked(key)
        out[key] = {"value": MASK if (value and key in SECRET_KEYS) else ("" if key in SECRET_KEYS else value),
                    "set": bool(locked or value), "locked": bool(locked)}
    for key in mailer.CONFIG_KEYS:
        value = db.get_setting(key) or ""
        locked = mailer.env_locked(key)
        out[key] = {"value": MASK if (value and key in SECRET_KEYS) else ("" if key in SECRET_KEYS else value),
                    "set": bool(locked or value), "locked": bool(locked)}
    out["signup_enabled"] = {"value": db.get_setting("signup_enabled") == "1", "locked": False}
    out["billing_trial_days"] = {"value": entitlement.trial_days(), "locked": False}
    out["billing_grace_days"] = {"value": entitlement.grace_days(), "locked": False}
    return jsonify(out)


@bp.put("/config")
@admin_required
def put_config():
    data = json_body()
    changed = []
    for key, value in data.items():
        if key == "signup_enabled":
            db.set_setting(key, "1" if value else "0")
            changed.append(key)
            continue
        if key in NUMERIC_KEYS:
            lo, hi = NUMERIC_KEYS[key]
            db.set_setting(key, str(to_int(value, key, lo=lo, hi=hi, required=True)))
            changed.append(key)
            continue
        if key not in STRIPE_KEYS and key not in mailer.CONFIG_KEYS:
            continue
        if billing.env_locked(key) or mailer.env_locked(key):
            continue                      # .env is the source of truth for this one
        if key in SECRET_KEYS and value == MASK:
            continue                      # re-saving the form must not blank the stored secret
        db.set_setting(key, "" if value is None else str(value))
        changed.append(key)
    if changed:
        billing._prices_cache["data"] = None
        audit("billing.config_update", {"fields": changed})
    return jsonify({"ok": True})


@bp.post("/email/test")
@admin_required
def email_test():
    to = (json_body().get("to") or "").strip()
    if not to:
        return api_error("Enter an address to send to")
    # Synchronous, and it returns the real SMTP error: an admin diagnosing SMTP needs the actual
    # message, and this route is admin-only.
    ok, err = mailer.send_template("test", to, username=session.get("username"))
    return jsonify({"ok": ok, "error": err})
