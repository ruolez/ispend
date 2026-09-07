"""Date parsing, statement period detection and year inference. Pure."""
import re
from datetime import date, datetime, timedelta

from dateutil import parser as du_parser

DATE_FORMATS = [
    "%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%Y%m%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y",
    "%m-%d-%Y", "%m-%d-%y", "%d/%m/%Y", "%d/%m/%y", "%d-%b-%Y", "%d-%b-%y", "%b %d %Y", "%Y/%m/%d",
    "%d-%m-%Y", "%b. %d, %Y", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S",
]
_DAY_FIRST = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y")
def _today():
    """Today in the app's timezone (UTC would flip the year around New Year for western users)."""
    try:
        from zoneinfo import ZoneInfo

        import config
        return datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()
    except Exception:
        return date.today()


_MONTH_DAY = re.compile(r"^(\d{1,2})[/-](\d{1,2})$")
_MON_DAY_TEXT = re.compile(r"^([A-Za-z]{3,9})\.?\s*(\d{1,2})$|^(\d{1,2})\s+([A-Za-z]{3,9})\.?$")

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _ordered_formats(day_first):
    if not day_first:
        return DATE_FORMATS
    first = [f for f in DATE_FORMATS if f in _DAY_FIRST]
    rest = [f for f in DATE_FORMATS if f not in _DAY_FIRST]
    return first + rest


def parse_date(value, fmt=None, day_first=False, default_year=None):
    """Return a date or None. Explicit fmt first, then ordered tries, then dateutil."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().strip('"').strip()
    if not text or not re.search(r"\d", text):
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}T", text):
        text = text.split("T", 1)[0]
    if fmt:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    for f in _ordered_formats(day_first):
        try:
            return datetime.strptime(text, f).date()
        except ValueError:
            continue
    m = _MONTH_DAY.match(text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        month, day = (b, a) if day_first else (a, b)
        if month > 12 and day <= 12:
            month, day = day, month
        year = default_year or _today().year
        try:
            return date(year, month, day)
        except ValueError:
            return None
    m = _MON_DAY_TEXT.match(text)
    if m:
        mon, day = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
        if mon.lower()[:3] in _MONTHS:
            year = default_year or _today().year
            try:
                return date(year, _MONTHS[mon.lower()[:3]], int(day))
            except ValueError:
                return None
    if len(text) < 6 or not re.search(r"[\d]{1,4}[/\-. ][\dA-Za-z]", text):
        return None
    try:
        return du_parser.parse(text, dayfirst=day_first, default=datetime(1900, 1, 1)).date()
    except (ValueError, OverflowError, TypeError):
        return None


_NUM_DATE = r"\d{1,2}/\d{1,2}/\d{2,4}"
_TXT_DATE = r"[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}"
_ISO_DATE = r"\d{4}-\d{2}-\d{2}"
_SEP = r"\s*(?:-|–|—|to|through|thru)\s*"
_RANGE_PATTERNS = [
    re.compile(rf"({_NUM_DATE}){_SEP}({_NUM_DATE})"),
    re.compile(rf"({_TXT_DATE}){_SEP}({_TXT_DATE})"),
    re.compile(rf"({_ISO_DATE}){_SEP}({_ISO_DATE})"),
    re.compile(rf"([A-Z][a-z]{{2,8}}\.? \d{{1,2}}){_SEP}({_TXT_DATE})"),
]
_OPEN_CLOSE = re.compile(
    rf"Opening\s+Date[:\s]+({_NUM_DATE}|{_TXT_DATE}).{{0,80}}?Closing\s+Date[:\s]+({_NUM_DATE}|{_TXT_DATE})", re.S)
_CLOSING = re.compile(rf"(?:Closing|Statement)\s+Date[:\s]+({_NUM_DATE}|{_TXT_DATE})")
_LAST_THIS = re.compile(rf"Last\s+statement:?\s+({_TXT_DATE}|{_NUM_DATE}).*?This\s+statement:?\s+({_TXT_DATE}|{_NUM_DATE})", re.I | re.S)


def find_period(text):
    """(start, end) from statement header text, or None."""
    m = _LAST_THIS.search(text or "")
    if m:
        a, b = parse_date(m.group(1)), parse_date(m.group(2))
        if a and b and a < b:
            return (a + timedelta(days=1), b)
    if not text:
        return None
    m = _OPEN_CLOSE.search(text)
    if m:
        a, b = parse_date(m.group(1)), parse_date(m.group(2))
        if a and b:
            return (min(a, b), max(a, b))
    for pat in _RANGE_PATTERNS:
        for m in pat.finditer(text):
            b = parse_date(m.group(2))
            a = parse_date(m.group(1), default_year=b.year if b else None)
            if a and b and abs((b - a).days) <= 400:
                return (min(a, b), max(a, b))
    m = _CLOSING.search(text)
    if m:
        b = parse_date(m.group(1))
        if b:
            return (b - timedelta(days=35), b)
    return None


def infer_year(month, day, period, today=None):
    """Year for an MM/DD-only row: the one that lands inside the statement period."""
    today = today or date.today()
    if period:
        start, end = period
        for year in sorted({start.year, end.year, end.year - 1}):
            try:
                d = date(year, month, day)
            except ValueError:
                continue
            if start <= d <= end:
                return year
        try:
            d = date(end.year, month, day)
            if d > end + timedelta(days=45):
                return end.year - 1
        except ValueError:
            pass
        return end.year
    try:
        d = date(today.year, month, day)
    except ValueError:
        return today.year
    return today.year - 1 if d > today + timedelta(days=30) else today.year


def apply_year(d, period, today=None):
    """Re-year a date parsed with a placeholder year (MM/DD rows)."""
    if d is None:
        return None
    return date(infer_year(d.month, d.day, period, today), d.month, d.day)
