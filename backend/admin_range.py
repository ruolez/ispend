"""The date range every admin chart and number is computed over.

Ranges are whole local days (APP_TIMEZONE), end exclusive, decided on the server so a cached
answer is keyed on the same thing no matter when the browser asked. The comparison period is the
same length immediately before.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import config

PRESETS = ("7d", "30d", "90d", "12m", "mtd", "ytd", "custom")
DEFAULT = "30d"
MAX_DAYS = 3 * 366


class RangeError(ValueError):
    """User-facing: shown as the 400 message."""


def today(tz=None):
    return datetime.now(ZoneInfo(tz or config.APP_TIMEZONE)).date()


def _date(value, name):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise RangeError(f"{name} must be a date like 2026-01-31") from None


def bucket_for(days):
    """Day up to a month, week up to half a year, month beyond: 7 to 31 points in a chart, never a
    wall of hairline bars."""
    if days <= 31:
        return "day"
    if days <= 186:
        return "week"
    return "month"


def _start_of(d, bucket):
    if bucket == "week":
        return d - timedelta(days=d.weekday())
    if bucket == "month":
        return d.replace(day=1)
    return d


def _next(d, bucket):
    if bucket == "day":
        return d + timedelta(days=1)
    if bucket == "week":
        return d + timedelta(days=7)
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def buckets(start, end, bucket):
    """[(bucket_start, bucket_end)] covering [start, end), the first and last clipped to it."""
    out = []
    cur = start
    while cur < end:
        nxt = min(_next(_start_of(cur, bucket), bucket), end)
        out.append((cur, nxt))
        cur = nxt
    return out


def parse(args, now_day=None, tz=None):
    """{key, start, end, prev_start, prev_end, days, bucket, compare} from request args
    (range, from, to, compare). Dates are local; end and prev_end are exclusive."""
    tz = tz or config.APP_TIMEZONE
    now_day = now_day or today(tz)
    key = args.get("range") or DEFAULT
    if key not in PRESETS:
        raise RangeError(f"range must be one of {', '.join(PRESETS)}")
    end = now_day + timedelta(days=1)
    if key in ("7d", "30d", "90d"):
        start = end - timedelta(days=int(key[:-1]))
    elif key == "12m":
        start = end - timedelta(days=365)
    elif key == "mtd":
        start = now_day.replace(day=1)
    elif key == "ytd":
        start = now_day.replace(month=1, day=1)
    else:
        start = _date(args.get("from"), "from")
        end = _date(args.get("to"), "to") + timedelta(days=1)
        if end <= start:
            raise RangeError("to must be on or after from")
        if (end - start).days > MAX_DAYS:
            raise RangeError("A custom range can be at most three years")
    days = (end - start).days
    compare = str(args.get("compare") or "").lower() in ("1", "true", "prev")
    return {"key": key, "start": start, "end": end, "days": days, "bucket": bucket_for(days),
            "prev_start": start - timedelta(days=days), "prev_end": start, "compare": compare, "tz": tz}


def at(d, tz):
    """Local midnight of d as an aware datetime (the SQL bound for a day)."""
    return datetime.combine(d, time.min, ZoneInfo(tz))


def cache_key(rng):
    return f"{rng['key']}:{rng['start']}:{rng['end']}:{int(rng['compare'])}"
