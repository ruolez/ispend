"""Where a sign-up came from, first-party only.

The landing page keeps the first visit's campaign tags and referring site in the visitor's own
browser (localStorage, 90 days) and hands them over with the sign-up form. Nothing is sent
anywhere else, no cookie is set for it, and only the referring *site* is kept, never the page.
"""

import re
from urllib.parse import urlparse

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content")
MAX_VALUE = 100
SEARCH = ("google.", "bing.", "duckduckgo.", "yahoo.", "ecosia.", "brave.", "startpage.", "kagi.")
SOCIAL = ("facebook.", "instagram.", "t.co", "twitter.", "x.com", "linkedin.", "reddit.", "youtube.",
          "tiktok.", "pinterest.", "threads.", "mastodon.", "bsky.", "news.ycombinator.")
_SAFE = re.compile(r"[^a-z0-9._~ +\-/]")


def _clean(value, lower=True):
    if not isinstance(value, str):
        return None
    value = value.strip()[:MAX_VALUE]
    value = value.lower() if lower else value
    value = _SAFE.sub("", value) if lower else re.sub(r"[\x00-\x1f\x7f<>]", "", value)
    return value or None


def _host(value):
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    host = urlparse(raw if "//" in raw else f"//{raw}").hostname or ""
    host = host.lower().removeprefix("www.")
    return host[:MAX_VALUE] if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host or "") else None


def channel(utm_source, utm_medium, referrer_host):
    """One word to group sign-ups by: the campaign's source, else search/social/the referring
    site, else direct."""
    if utm_source:
        return utm_source
    if referrer_host:
        if any(referrer_host.startswith(s) or f".{s}" in f".{referrer_host}" for s in SEARCH):
            return "search"
        if any(referrer_host.startswith(s) or f".{s}" in f".{referrer_host}" for s in SOCIAL):
            return "social"
        return referrer_host
    if utm_medium:
        return utm_medium
    return "direct"


def clean(payload, own_host=None):
    """The attribution row to store from what the browser sent, or None when there is nothing."""
    if not isinstance(payload, dict):
        return None
    out = {k: _clean(payload.get(k)) for k in UTM_KEYS}
    ref = _host(payload.get("referrer_host") or payload.get("referrer"))
    if ref and own_host and (ref == own_host or ref.endswith(f".{own_host}")):
        ref = None                   # moving around our own site is not a source
    path = payload.get("landing_path")
    path = _clean(path, lower=False) if isinstance(path, str) and path.startswith("/") else None
    out.update({"referrer_host": ref, "landing_path": path.split("?")[0][:MAX_VALUE] if path else None})
    first_seen = payload.get("first_seen_at")
    out["first_seen_at"] = first_seen if isinstance(first_seen, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?", first_seen) else None
    out["channel"] = channel(out["utm_source"], out["utm_medium"], ref)
    return out
