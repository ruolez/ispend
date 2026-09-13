"""The only unauthenticated read surface in the app: what the landing page needs to render itself.

Deliberately undecorated — a GET, so `entitlement.enforce_write_access` ignores it, and it exposes
nothing an anonymous visitor could not already learn from the sign-in page: whether sign-ups are
open, how long the trial is, and the public list prices. Stripe identifiers are stripped, and a
Stripe outage degrades to "no plans" rather than a 500, because this page must render for a
self-hosted install with no Stripe at all.
"""

import logging

from flask import Blueprint, jsonify

import billing
import db
import entitlement
import landing

log = logging.getLogger(__name__)

bp = Blueprint("public", __name__, url_prefix="/api/public")

APP_NAME = "iSpend"
PUBLIC_PRICE_FIELDS = ("plan", "amount_cents", "currency", "interval")


def public_prices():
    if not billing.enabled():
        return []
    try:
        rows = billing.prices()
    except Exception as e:                       # noqa: BLE001 - the page renders without prices
        log.warning("public landing could not read Stripe prices: %s", e)
        return []
    return [{k: row.get(k) for k in PUBLIC_PRICE_FIELDS} for row in rows]


@bp.get("/landing")
def landing_config():
    return jsonify({
        "app_name": APP_NAME,
        "signup_enabled": db.get_setting("signup_enabled") == "1",
        "billing_enabled": billing.enabled(),
        "trial_days": entitlement.trial_days(),
        "prices": public_prices(),
        "landing": landing.load(),
    })
