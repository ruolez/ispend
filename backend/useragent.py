"""Just enough user-agent parsing to label a sign-in ("Safari 18 on iOS, mobile").

Deliberately small and dependency-free: the result is for a human skimming a sign-in history, not
for analytics that need every browser. Order matters in every table — Edge and Opera also claim to
be Chrome, and Chrome also claims to be Safari.
"""

import re

_BROWSERS = (
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/(\d+)")),
    ("Opera", re.compile(r"(?:OPR|Opera)/(\d+)")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/(\d+)")),
    ("Firefox", re.compile(r"(?:Firefox|FxiOS)/(\d+)")),
    ("Chrome", re.compile(r"(?:Chrome|CriOS)/(\d+)")),
    ("Safari", re.compile(r"Version/(\d+)[.\d]* (?:Mobile/\S+ )?Safari/")),
)
_OS = (
    ("iOS", re.compile(r"iPhone|iPad|iPod")),
    ("Android", re.compile(r"Android")),
    ("Windows", re.compile(r"Windows NT")),
    ("ChromeOS", re.compile(r"CrOS")),
    ("macOS", re.compile(r"Mac OS X|Macintosh")),
    ("Linux", re.compile(r"Linux")),
)
_BOT = re.compile(r"bot|crawl|spider|slurp|curl/|wget/|python-requests|httpx|go-http-client|headless",
                  re.IGNORECASE)
_TABLET = re.compile(r"iPad|Tablet|Android(?!.*Mobile)")
_MOBILE = re.compile(r"Mobi|iPhone|iPod|Android.*Mobile")


def parse(ua):
    """{browser, os, device} for a User-Agent header; None fields when nothing matched."""
    ua = (ua or "")[:512]
    if not ua:
        return {"browser": None, "os": None, "device": "unknown"}
    browser = None
    for name, pattern in _BROWSERS:
        m = pattern.search(ua)
        if m:
            browser = f"{name} {m.group(1)}"
            break
    os_name = next((name for name, pattern in _OS if pattern.search(ua)), None)
    if _BOT.search(ua):
        device = "bot"
    elif _TABLET.search(ua):
        device = "tablet"
    elif _MOBILE.search(ua):
        device = "mobile"
    elif os_name or browser:
        device = "desktop"
    else:
        device = "unknown"
    return {"browser": browser, "os": os_name, "device": device}


def label(parsed):
    """'Chrome 128 on macOS' / 'Safari 18 on iOS' / 'Unknown device'."""
    parts = [p for p in (parsed.get("browser"), parsed.get("os")) if p]
    return " on ".join(parts) if parts else "Unknown device"
