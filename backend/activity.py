"""Which days each person used iSpend, and for what.

One row per user per day, with a bit per kind of use. It is what the admin's active-user counts,
retention cohorts and activation funnel are computed from — counts of people and days, never what
they looked at. "Active" means a value event (importing, sorting transactions, reading a report);
merely opening the app is recorded too, but separately, because a finance app people open once a
month to import a statement would otherwise look dead or look busy depending on the definition.

Written from an after_request hook keyed on the matched route, so no endpoint has to remember to
call it, and memoised per worker so a busy session costs one upsert per kind per day.
"""

import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import db

log = logging.getLogger(__name__)

SEEN = 1
IMPORT = 2
CATEGORIZE = 4
REPORT = 8
DASHBOARD = 16
UPLOAD = 32
VALUE_MASK = IMPORT | CATEGORIZE | REPORT
NAMES = {SEEN: "seen", IMPORT: "import", CATEGORIZE: "categorize", REPORT: "report",
         DASHBOARD: "dashboard", UPLOAD: "upload"}

# (method, Flask rule) -> bit. Only successful responses count.
ROUTES = {
    ("POST", "/api/statements"): UPLOAD,
    ("POST", "/api/statements/<int:statement_id>/commit"): IMPORT,
    ("PUT", "/api/transactions/<int:txn_id>"): CATEGORIZE,
    ("POST", "/api/transactions/bulk"): CATEGORIZE,
    ("POST", "/api/transactions/pair"): CATEGORIZE,
    ("PUT", "/api/transactions/<int:txn_id>/splits"): CATEGORIZE,
    ("POST", "/api/review/resolve"): CATEGORIZE,
    ("POST", "/api/rules"): CATEGORIZE,
    ("POST", "/api/rules/run"): CATEGORIZE,
    ("POST", "/api/rules/<int:rule_id>/apply"): CATEGORIZE,
    ("GET", "/api/reports/summary"): REPORT,
    ("GET", "/api/reports/by-category"): REPORT,
    ("GET", "/api/reports/monthly"): REPORT,
    ("GET", "/api/reports/trends"): REPORT,
    ("GET", "/api/reports/top-merchants"): REPORT,
    ("GET", "/api/reports/month-over-month"): REPORT,
    ("GET", "/api/reports/recurring"): REPORT,
    ("GET", "/api/insights"): REPORT,
    ("GET", "/api/budgets/progress"): REPORT,
    ("GET", "/api/reports/dashboard"): DASHBOARD,
}

_memo = {}
_memo_lock = threading.Lock()


def today():
    return datetime.now(ZoneInfo(config.APP_TIMEZONE)).date()


def _fresh(uid, day, bits):
    """The bits not yet written for (uid, day) by this worker; records them as written."""
    with _memo_lock:
        if len(_memo) > 50_000:      # a long-lived worker drops yesterday's keys wholesale
            for key in [k for k in _memo if k[1] != day]:
                del _memo[key]
        have = _memo.get((uid, day), 0)
        new = bits & ~have
        if new:
            _memo[(uid, day)] = have | new
        return new


UPSERT = """
INSERT INTO user_activity_days (user_id, day, kinds) VALUES (%s, %s, %s)
ON CONFLICT (user_id, day) DO UPDATE SET kinds = user_activity_days.kinds | EXCLUDED.kinds
 WHERE (user_activity_days.kinds & EXCLUDED.kinds) <> EXCLUDED.kinds
"""


def touch(uid, bits):
    """Mark uid as having done `bits` today. Returns the bits actually written."""
    if not uid or not bits:
        return 0
    day = today()
    new = _fresh(uid, day, bits)
    if not new:
        return 0
    db.execute(UPSERT, (uid, day, new))
    if new & IMPORT:
        db.execute("""UPDATE users SET first_commit_at = COALESCE(first_commit_at, now()),
                                       last_active_at = now() WHERE id = %s""", (uid,))
    elif new & VALUE_MASK:
        db.execute("UPDATE users SET last_active_at = now() WHERE id = %s", (uid,))
    if new & UPLOAD:
        db.execute("UPDATE users SET first_upload_at = COALESCE(first_upload_at, now()) WHERE id = %s", (uid,))
    return new


def bits_for(method, rule):
    return ROUTES.get((method, rule), 0)


def from_request(request, response, uid):
    """after_request: never lets bookkeeping break the response."""
    try:
        if not uid or response.status_code >= 400 or request.url_rule is None:
            return
        bits = bits_for(request.method, request.url_rule.rule)
        if bits:
            touch(uid, bits)
    except Exception:
        log.warning("could not record activity", exc_info=True)
