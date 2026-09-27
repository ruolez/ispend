"""The only unauthenticated read surface in the app: what the landing page needs to render itself.

Deliberately undecorated — a GET, so `entitlement.enforce_write_access` ignores it, and it exposes
nothing an anonymous visitor could not already learn from the sign-in page: whether sign-ups are
open, how long the trial is, and the public list prices. Stripe identifiers are stripped, and a
Stripe outage degrades to "no plans" rather than a 500, because this page must render for a
self-hosted install with no Stripe at all.
"""

import logging

from flask import Blueprint, Response, jsonify, request

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


# ---------- one-click unsubscribe from news (RFC 8058) ----------

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · iSpend</title><link rel="stylesheet" href="/css/tokens.css"><link rel="stylesheet" href="/css/app.css"></head>
<body><main class="main" style="max-width:520px;margin:10vh auto;padding:0 16px"><div class="card"><div class="card-body">
<h1 style="margin-bottom:12px">{title}</h1><p class="text-2">{text}</p>{form}</div></div></main></body></html>"""


def _page(title, text, form="", status=200):
    import html
    return Response(_PAGE.format(title=html.escape(title), text=html.escape(text), form=form), status=status,
                    mimetype="text/html")


@bp.route("/unsubscribe", methods=["GET", "POST"])
def unsubscribe():
    """GET shows a button (link scanners must not unsubscribe people); POST — the button, or a mail
    client's one-click request — records the choice. Account emails (receipts, password resets)
    are not affected: only news is suppressed."""
    import html

    import admin_email
    token = request.args.get("t") or request.form.get("t") or ""
    uid = admin_email.user_for_token(token)
    user = db.query("SELECT email FROM users WHERE id = %s", (uid,), one=True) if uid else None
    if not user or not user.get("email"):
        return _page("Link not recognised", "This unsubscribe link is not valid any more. Nothing was changed.", status=404)
    if request.method == "GET":
        form = (f'<form method="post" action="/api/public/unsubscribe?t={html.escape(token)}" style="margin-top:16px">'
                '<button type="submit" class="btn btn-primary">Unsubscribe from news</button></form>')
        return _page("Stop news from iSpend?", "You will still get emails about your own account, like receipts and "
                     "password resets.", form)
    db.execute("""INSERT INTO email_suppressions (email_sha256, reason) VALUES (%s, 'unsubscribed')
                  ON CONFLICT (email_sha256) DO NOTHING""", (admin_email.email_hash(user["email"]),))
    return _page("You are unsubscribed", "iSpend will not send you news again. Emails about your own account "
                 "still arrive.")
