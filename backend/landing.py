"""Marketing copy for the public landing page.

One JSON row in `settings` rather than a table: the shape is fixed, there is exactly one of it, and
it matches how `signup_enabled` and `billing_trial_days` already live. Everything an admin types
passes through `normalize()` on the way in *and* on the way out, so a corrupt or hand-edited row can
never reach the public page, and the page always receives a complete object it can render blindly.
Amounts are never stored here — those come live from Stripe via `billing.prices()`.
"""

import json
import re

import db

SETTING_KEY = "landing_pricing"

MAX_FEATURES = 12
LIMITS = {"heading": 60, "sub": 160, "name": 40, "tagline": 120, "badge": 24, "cta_label": 32,
          "feature": 120, "yearly_note": 32, "footnote": 200}

DEFAULT_LANDING = {
    "heading": "One plan. Everything in it.",
    "sub": "Try the whole thing free first. No card up front, and canceling takes two clicks.",
    "paid": {
        "name": "iSpend",
        "tagline": "Every feature, for one honest price. Usually less than one forgotten subscription.",
        "badge": "",
        "features": [
            "As many statements and accounts as you like",
            "Spreadsheets, CSVs and PDFs, scans included",
            "12 banks with no setup, and any other bank too",
            "Learns how you sort things and does it for you",
            "Every subscription found and totalled for the year",
            "Budgets, labels, split receipts, month-on-month trends",
            "AI insights if you want them, off if you don't",
            "Download everything, whenever you like",
        ],
        "cta_label": "Start free trial",
    },
    "yearly_note": "2 months free",
    "footnote": "Cancel any time, in a couple of clicks. Whatever you decide, your data stays yours "
                "and stays downloadable.",
}

_TAGS = re.compile(r"<[^>]*>")
_SPACE = re.compile(r"\s+")


def _clean(value, limit):
    """Plain text: no markup, no runs of whitespace, no control characters, capped in length."""
    if not isinstance(value, str):
        return ""
    text = _SPACE.sub(" ", _TAGS.sub(" ", value)).strip()
    return text[:limit].strip()


def _text(src, key, default, clearable=False):
    """`clearable` fields keep an admin's deliberate blank; the rest fall back to the shipped copy."""
    if key not in src:
        return default
    text = _clean(src.get(key), LIMITS[key])
    if text:
        return text
    return "" if clearable else default


def _features(src, default):
    if "features" not in src:
        return list(default)
    given = src.get("features")
    if not isinstance(given, list):
        return list(default)
    out = []
    for item in given:
        text = _clean(item, LIMITS["feature"])
        if text:
            out.append(text)
        if len(out) == MAX_FEATURES:
            break
    return out


def normalize(raw):
    """Return a complete, safe copy blob from anything at all — a dict, a JSON string, or junk."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    paid = raw.get("paid") if isinstance(raw.get("paid"), dict) else {}
    dpaid = DEFAULT_LANDING["paid"]
    return {
        "heading": _text(raw, "heading", DEFAULT_LANDING["heading"]),
        "sub": _text(raw, "sub", DEFAULT_LANDING["sub"], clearable=True),
        "paid": {
            "name": _text(paid, "name", dpaid["name"]),
            "tagline": _text(paid, "tagline", dpaid["tagline"], clearable=True),
            "badge": _text(paid, "badge", dpaid["badge"], clearable=True),
            "features": _features(paid, dpaid["features"]),
            "cta_label": _text(paid, "cta_label", dpaid["cta_label"]),
        },
        "yearly_note": _text(raw, "yearly_note", DEFAULT_LANDING["yearly_note"], clearable=True),
        "footnote": _text(raw, "footnote", DEFAULT_LANDING["footnote"], clearable=True),
    }


def load():
    return normalize(db.get_setting(SETTING_KEY))


def save(data):
    clean = normalize(data)
    db.set_setting(SETTING_KEY, json.dumps(clean, ensure_ascii=False))
    return clean
