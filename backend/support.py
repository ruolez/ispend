"""Problem reports: the rules both sides of a thread share. No database access here.

A report is a thread between one customer and the operator. The customer's diagnostics arrive
as a small JSON document; only allow-listed fields are kept, and anything that could be money,
an account number or an address is masked before it is stored — the customer is told exactly
what is sent, and this is the server's half of that promise.
"""

import json
import re
from datetime import timedelta
from urllib.parse import urlsplit

KINDS = ("bug", "data", "question", "idea", "billing")
STATUSES = ("open", "in_progress", "waiting", "resolved")
IMPACTS = ("blocking", "annoying", "minor")

SUBJECT_MAX = 140
SUBJECT_AUTO_MAX = 80
BODY_MIN = 10
BODY_MAX = 8000
REOPEN_DAYS = 30
REF_OFFSET = 1000

CONTEXT_LIST_MAX = 10
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANK_RUN = re.compile(r"\n{3,}")
_REF = re.compile(r"^#?\s*r?-?\s*(\d+)$", re.IGNORECASE)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_AMOUNT = re.compile(
    r"[-+]?[$€£]?\s?\d{1,3}(?:,\d{3})+(?:\.\d{2})?"
    r"|[-+]?[$€£]\s?\d+(?:\.\d{2})?"
    r"|[-+]?\d+\.\d{2}\b")
_DIGITS = re.compile(r"\d{4,}")

_REQUEST_ID = re.compile(r"^[0-9a-f]{8,32}$")
_VIEWPORT = re.compile(r"^\d{2,5}x\d{2,5}(@[\d.]{1,4})?$")


def ref(report_id):
    """What the customer and the operator call a report: R-1042."""
    return f"R-{int(report_id) + REF_OFFSET}"


def parse_ref(text):
    m = _REF.match(str(text or "").strip())
    if not m:
        return None
    n = int(m.group(1)) - REF_OFFSET
    return n if n > 0 else None


def clean_text(value):
    text = _CONTROL.sub("", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return _BLANK_RUN.sub("\n\n", text).strip()


def _one_line(value):
    return " ".join(_CONTROL.sub("", str(value or "")).split())


def subject_from(body):
    first = _one_line(body.split("\n", 1)[0])
    if len(first) <= SUBJECT_AUTO_MAX:
        return first
    cut = first[:SUBJECT_AUTO_MAX + 1].rsplit(" ", 1)[0] or first[:SUBJECT_AUTO_MAX]
    return cut[:SUBJECT_AUTO_MAX].rstrip(" ,.;:") + "…"


def _body_problem(body):
    if not body:
        return "Tell us what happened"
    if len(body) > BODY_MAX:
        return f"Keep it under {BODY_MAX} characters"
    return None


def validate_report(data):
    """(clean report, None) or (None, the problem in words a customer understands)."""
    kind = data.get("kind")
    if kind not in KINDS:
        return None, "Choose what kind of report this is"
    body = clean_text(data.get("body"))
    problem = _body_problem(body)
    if problem:
        return None, problem
    if len(body) < BODY_MIN:
        return None, f"Add a little more detail (at least {BODY_MIN} characters)"
    subject = _one_line(data.get("subject"))
    if len(subject) > SUBJECT_MAX:
        return None, f"Keep the subject under {SUBJECT_MAX} characters"
    impact = data.get("impact") or None
    if impact is not None and impact not in IMPACTS:
        return None, "Invalid impact"
    return {"kind": kind, "subject": subject or subject_from(body), "body": body, "impact": impact}, None


def validate_message(data, has_files=False):
    body = clean_text(data.get("body"))
    if not body:
        return ("(screenshot)", None) if has_files else (None, "Write a message")
    if len(body) > BODY_MAX:
        return None, f"Keep it under {BODY_MAX} characters"
    return body, None


def redact(text):
    """Mask what could identify a person's money in free text we did not write ourselves."""
    text = _EMAIL.sub("[email]", str(text or ""))
    text = _CARD.sub("[number]", text)
    text = _AMOUNT.sub("[amount]", text)
    return _DIGITS.sub("[number]", text)


def template_path(value):
    """A URL or path without origin, query, fragment or numeric ids: /api/transactions/:id."""
    raw = str(value or "")
    if not raw:
        return ""
    parts = urlsplit(raw)
    path = parts.path or ""
    return "/".join(":id" if seg.isdigit() else seg for seg in path.split("/"))[:200]


def _short(value, limit):
    return value[:limit] if isinstance(value, str) and value else None


def _clean_error(item):
    if not isinstance(item, dict):
        return None
    out = {"message": redact(item["message"])[:300] if isinstance(item.get("message"), str) else None,
           "source": template_path(item["source"]) if isinstance(item.get("source"), str) else None,
           "at": _short(item.get("at"), 40)}
    return {k: v for k, v in out.items() if v} or None


def _clean_request(item):
    if not isinstance(item, dict):
        return None
    method = str(item.get("method") or "").upper()
    status = item.get("status")
    rid = item.get("request_id")
    out = {"method": method if method in METHODS else None,
           "path": template_path(item["path"]) if isinstance(item.get("path"), str) else None,
           "status": status if isinstance(status, int) and not isinstance(status, bool) and 0 <= status <= 599 else None,
           "request_id": rid if isinstance(rid, str) and _REQUEST_ID.match(rid) else None,
           "message": redact(item["message"])[:200] if isinstance(item.get("message"), str) else None,
           "at": _short(item.get("at"), 40)}
    return {k: v for k, v in out.items() if v is not None and v != ""} or None


def _clean_list(value, fn):
    if not isinstance(value, list):
        return None
    items = [c for c in (fn(v) for v in value[:CONTEXT_LIST_MAX * 3]) if c][:CONTEXT_LIST_MAX]
    return items or None


def clean_context(raw):
    """The allow-listed, redacted diagnostics a report keeps. Unknown keys are dropped."""
    if not isinstance(raw, dict):
        return {}
    out = {
        "page": template_path(raw["page"]) if isinstance(raw.get("page"), str) else None,
        "app_version": _short(raw.get("app_version"), 40),
        "build": _short(raw.get("build"), 40),
        "viewport": raw["viewport"] if isinstance(raw.get("viewport"), str) and _VIEWPORT.match(raw["viewport"]) else None,
        "theme": raw["theme"] if raw.get("theme") in ("light", "dark") else None,
        "standalone": raw["standalone"] if isinstance(raw.get("standalone"), bool) else None,
        "online": raw["online"] if isinstance(raw.get("online"), bool) else None,
        "language": _short(raw.get("language"), 20),
        "timezone": _short(raw.get("timezone"), 60),
        "request_id": raw["request_id"] if isinstance(raw.get("request_id"), str) and _REQUEST_ID.match(raw["request_id"]) else None,
        "errors": _clean_list(raw.get("errors"), _clean_error),
        "requests": _clean_list(raw.get("requests"), _clean_request),
    }
    return {k: v for k, v in out.items() if v is not None and v != ""}


def context_json(context):
    return json.dumps(context, separators=(",", ":"), sort_keys=True)


# (actor, action) -> the status it leads to; None keeps the current one.
_TRANSITIONS = {
    ("user", "reply"): "open",
    ("user", "resolve"): "resolved",
    ("admin", "reply"): "waiting",
    ("admin", "reply_resolve"): "resolved",
    ("admin", "note"): None,
}


def can_reopen(resolved_at, now):
    return resolved_at is None or now - resolved_at <= timedelta(days=REOPEN_DAYS)


def next_status(current, actor, action, resolved_at, now):
    """The status after this message. None: the customer may not reopen a report that was
    resolved more than REOPEN_DAYS ago — a new report is clearer for both sides by then."""
    if (actor, action) not in _TRANSITIONS:
        raise ValueError(f"unknown support action {actor}/{action}")
    target = _TRANSITIONS[(actor, action)]
    if (actor, action) == ("user", "reply"):
        if current == "resolved":
            return "open" if can_reopen(resolved_at, now) else None
        return "open" if current == "waiting" else current
    return current if target is None else target
