"""Who may write, and why.

evaluate() is pure: no DB, no session, no flask.g. That is what makes it testable against every
Stripe status and every time boundary, and it is the only place the rules live.

enforce_write_access() is the single gate. It blocks every non-GET outside an explicit allowlist
rather than relying on a decorator, because "we forgot to decorate the new endpoint" is exactly
the failure that leaks the product: there are ~47 mutating routes today and one more with every
change. Inverting the default means a new endpoint is safe the day it is written.
"""

import logging
import math
import re
from datetime import datetime, timedelta, timezone

from flask import g, jsonify, request, session

import db

log = logging.getLogger(__name__)

ADMIN_EXEMPT, TRIALING, ACTIVE, GRACE, READ_ONLY = (
    "admin_exempt", "trialing", "active", "grace", "read_only")

DEFAULT_TRIAL_DAYS = 14
DEFAULT_GRACE_DAYS = 7

# Stripe statuses that mean "not paid right now".
LAPSED_STATUSES = {"past_due", "unpaid", "incomplete", "incomplete_expired", "canceled", "paused"}
GOOD_STATUSES = {"active", "trialing"}

ERROR_CODE = "subscription_required"
ERROR_MESSAGE = ("Your iSpend subscription has ended. You can still view and export everything — "
                 "subscribe to make changes again.")


def _setting_int(key, default, lo, hi):
    try:
        return max(lo, min(hi, int(db.get_setting(key))))
    except (TypeError, ValueError):
        return default


def trial_days():
    return _setting_int("billing_trial_days", DEFAULT_TRIAL_DAYS, 1, 90)


def grace_days():
    return _setting_int("billing_grace_days", DEFAULT_GRACE_DAYS, 0, 90)


def billing_enabled():
    import billing
    return billing.enabled()


def evaluate(row, now=None, enabled=None, trial=None, grace=None):
    """row is one joined users/subscriptions dict. Returns the entitlement dict."""
    now = now or datetime.now(timezone.utc)
    enabled = billing_enabled() if enabled is None else enabled
    trial = trial_days() if trial is None else trial
    grace = grace_days() if grace is None else grace
    row = row or {}

    def out(state, **extra):
        return {"state": state, "can_write": state != READ_ONLY, "billing_enabled": enabled,
                "status": row.get("sub_status") or row.get("status"),
                "plan": row.get("plan"),
                "trial_end": row.get("trial_end"),
                "current_period_end": row.get("current_period_end"),
                "grace_until": row.get("grace_until"),
                "cancel_at_period_end": bool(row.get("cancel_at_period_end")),
                "has_subscription": bool(row.get("stripe_subscription_id")),
                "comped": False, "days_left": None, **extra}

    # 1. Not configured: self-hosted and dev installs behave exactly as they always did.
    if not enabled:
        return out(ACTIVE)

    # 2. The operator of a self-hosted install IS the admin; locking them out of their own admin
    #    console because a Stripe key expired would be unrecoverable without shell access.
    #    In SaaS mode this makes `admin` a free lifetime licence -- never grant it to a customer.
    if row.get("role") == "admin":
        return out(ADMIN_EXEMPT)

    comped = row.get("comped_until")
    if comped and comped > now:
        return out(ACTIVE, comped=True)

    status = row.get("sub_status") or row.get("status") or "none"
    trial_end = row.get("trial_end")
    # Self-heal a missing subscription row: an account with no Stripe object gets its trial
    # measured from when it was created.
    if trial_end is None and status in ("none", "trialing", "comped") and not row.get("stripe_subscription_id"):
        created = row.get("created_at")
        if created:
            trial_end = created + timedelta(days=trial)

    anchor = None
    if status == "trialing" or (status in ("none", "comped") and trial_end):
        if trial_end and trial_end > now:
            return out(TRIALING, trial_end=trial_end, days_left=_days_left(trial_end, now))
        anchor = trial_end
    elif status == "active":
        period_end = row.get("current_period_end")
        if period_end is None or period_end > now:
            return out(ACTIVE)
        anchor = period_end          # backstop for a webhook we never received
    elif status in LAPSED_STATUSES:
        anchor = row.get("lapsed_at") or row.get("current_period_end") or trial_end
    else:
        # A status Stripe invented after this shipped. Fail SOFT: failing closed would lock out
        # every paying customer at once, a self-inflicted outage with no warning. This gives a
        # grace window plus a loud log line and a counter on the admin billing summary.
        log.warning("unknown Stripe subscription status %r for user %s", status, row.get("id"))
        anchor = now

    computed = (anchor or now) + timedelta(days=grace)
    stored = row.get("grace_until")
    # max(): an admin's manual extension must win, but a stale value left by a missed webhook
    # must not. Correct in both directions, which is what makes the column safe to trust.
    grace_until = max(stored, computed) if stored else computed
    if now < grace_until:
        return out(GRACE, grace_until=grace_until, days_left=_days_left(grace_until, now))
    return out(READ_ONLY, grace_until=grace_until)


def _days_left(when, now):
    return max(0, math.ceil((when - now).total_seconds() / 86400))


def current():
    return getattr(g, "entitlement", None) or {"state": ACTIVE, "can_write": True,
                                               "billing_enabled": False}


def public_json(ent):
    """The subset handed to a browser."""
    keys = ("state", "can_write", "billing_enabled", "status", "plan", "trial_end",
            "current_period_end", "grace_until", "cancel_at_period_end", "has_subscription",
            "comped", "days_left")
    out = {}
    for k in keys:
        v = (ent or {}).get(k)
        out[k] = v.isoformat() if isinstance(v, datetime) else v
    return out


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Non-GET endpoints that keep working for a read-only account. Everything NOT listed is blocked,
# so an endpoint added next year is protected without anyone remembering to protect it. Adding
# to this list is the deliberate, reviewable act.
WRITE_ALLOWLIST = frozenset({
    # public auth
    "/api/auth/login", "/api/auth/logout", "/api/auth/signup",
    "/api/auth/password/forgot", "/api/auth/password/reset",
    "/api/auth/email/verify", "/api/auth/email/resend",
    # a user's own account: preferences and security must never be trapped behind a paywall
    "/api/auth/me/preferences", "/api/auth/me/password", "/api/auth/me/email",
    # a locked-out user MUST be able to pay
    "/api/billing/checkout", "/api/billing/portal", "/api/billing/refresh", "/api/billing/webhook",
    # verified read-only POSTs: these write nothing
    "/api/rules/preview", "/api/settings/openrouter/test",
})
# admin_required owns these, and the acting admin is admin_exempt anyway.
WRITE_ALLOWLIST_PREFIXES = ("/api/admin/",)
_READ_ONLY_POST = re.compile(r"^/api/transactions/\d+/rule-draft$")


def write_allowed(path):
    path = (path or "/").rstrip("/") or "/"
    return (path in WRITE_ALLOWLIST
            or path.startswith(WRITE_ALLOWLIST_PREFIXES)
            or bool(_READ_ONLY_POST.match(path)))


def enforce_write_access():
    if request.method in SAFE_METHODS:
        return None
    if "user_id" not in session:
        return None                      # 401 is login_required's job, not this gate's
    if write_allowed(request.path):
        return None
    ent = current()
    if ent.get("can_write"):
        return None
    return jsonify({"error": ERROR_MESSAGE, "code": ERROR_CODE, "state": ent.get("state"),
                    "billing_url": "/billing.html"}), 402
