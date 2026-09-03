"""Turn pdfplumber word boxes into text lines and transaction rows. Pure."""
import re
import statistics
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from bank_profiles.base import GENERIC_LINE, GENERIC_SKIP
from importer.amounts import parse_amount
from importer.dates import apply_year, parse_date
from importer.models import ParsedRow

_DATE_START = re.compile(r"^(\d{1,2}/\d{1,2}(?:/\d{2,4})?|[A-Z][a-z]{2}\.? \d{1,2}|\d{1,2} [A-Z][a-z]{2}|[A-Z]{3}\d{1,2})\b")
_HEADING = re.compile(r"^[A-Z][A-Z &,'/\-]{3,}$")


@dataclass
class Line:
    text: str
    top: float
    x0: float
    x1: float
    page: int
    words: list = field(default_factory=list)


def cluster_rows(words, y_tol=3.0, page=1):
    """Group word boxes into lines by their `top` coordinate."""
    ws = sorted(words, key=lambda w: (float(w["top"]), float(w["x0"])))
    lines, current, anchor = [], [], None
    for w in ws:
        top = float(w["top"])
        if anchor is None or abs(top - anchor) > y_tol:
            if current:
                lines.append(_make_line(current, page))
            current, anchor = [w], top
        else:
            current.append(w)
    if current:
        lines.append(_make_line(current, page))
    return lines


def _make_line(words, page):
    words = sorted(words, key=lambda w: float(w["x0"]))
    text = " ".join(str(w["text"]) for w in words)
    return Line(text=re.sub(r"\s+", " ", text).strip(), top=float(words[0]["top"]), x0=float(words[0]["x0"]),
                x1=float(words[-1]["x1"]), page=page, words=words)


def _section_mode(profile, text):
    for pattern, mode in (profile.pdf_sections if profile else ()):
        if pattern.match(text):
            return mode
    return None


def _apply_sign(value, amt_text, mode, default_mode):
    if re.search(r"\bCR\b", amt_text.upper()):
        return abs(value)
    if mode == "credit":
        return abs(value)
    if mode == "charge":
        if value > 0:
            return -value
        return abs(value) if default_mode == "flip" else value
    if mode == "flip":
        return -value
    return value


def _date_word_count(m):
    n = len(m.group("date").split())
    post = m.groupdict().get("post")
    if post:
        n += len(post.split())
    return n



_WELL_FORMED_TAIL = re.compile(r"\d\.\d{2}\)?(?:\s?CR)?-?\s*$")
_OCR_TAIL_SEP = re.compile(r"(?:^|\s)(\(?-?\$?\s?[\d,]*\d)\s*[.\-,\u00b7]\s*(\d{2})\)?\s*$")
_DETACHED_SIGN = re.compile(r"(?<=\s)-\s+(?=\(?\$?\d[\d,]*\.\d{2}\)?(?:\s?CR)?\s*$)")
_OCR_TAIL_DOLLAR = re.compile(r"(?:^|\s)(\(?-?\$\s?[\d,]*\d)\s+(\d{2})\)?\s*$")


def ocr_repair(text):
    """Re-join amounts that OCR split at the decimal point: '$84. 12', '$412 - 60', '$6 45' -> '$84.12' ...
    Lines that already end in a well-formed amount are returned untouched."""
    text = text.replace("\u2212", "-")
    if not _WELL_FORMED_TAIL.search(text):
        m = _OCR_TAIL_SEP.search(text) or _OCR_TAIL_DOLLAR.search(text)
        if m:
            head = text[:m.start()].rstrip()
            whole = m.group(1).replace(" ", "")
            text = f"{head} {whole}.{m.group(2)}".strip()
    # a detached sign token before the amount: "THANK YOU - $500.00" -> "THANK YOU -$500.00"
    return _DETACHED_SIGN.sub("-", text)

def rows_to_transactions(lines, profile, period, today=None, flip_sign=False):
    """State machine over lines: section headings set the sign mode, matching lines become rows,
    indented non-matching lines extend the previous description."""
    line_re = profile.pdf_line if (profile and profile.pdf_line) else GENERIC_LINE
    default_mode = profile.pdf_sign if profile else "as_is"
    mode = default_mode
    default_year = period[1].year if period else (today or date.today()).year
    rows, desc_x0s, continuation, unsigned_rows = [], [], 2, []
    for idx, line in enumerate(lines):
        text = ocr_repair(line.text.strip())
        if not text:
            continue
        if profile and profile.pdf_stop and profile.pdf_stop.match(text):
            break
        section = _section_mode(profile, text)
        if section:
            mode = default_mode if section == "as_is" else section
            continuation = 2
            continue
        if GENERIC_SKIP.match(text) or (profile and profile.pdf_skip and profile.pdf_skip.match(text)):
            continuation = 2
            continue
        m = line_re.match(text)
        if not m and line_re is not GENERIC_LINE:
            m = GENERIC_LINE.match(text)
        if m:
            amt_text = m.group("amt")
            value = parse_amount(amt_text)
            txn_date = parse_date(m.group("date"), default_year=default_year)
            if txn_date is not None and not re.search(r"\d{4}|\d{2}/\d{2}/\d{2}", m.group("date")):
                txn_date = apply_year(txn_date, period, today)
            post = m.groupdict().get("post")
            posted = parse_date(post, default_year=default_year) if post else None
            if posted and not re.search(r"\d{4}|\d{2}/\d{2}/\d{2}", post):
                posted = apply_year(posted, period, today)
            desc = m.group("desc").strip()
            problems = []
            if value is None:
                problems.append(f"Unparseable amount '{amt_text}'")
                amount = None
            else:
                amount = _apply_sign(value, amt_text, mode, default_mode)
                if flip_sign:
                    amount = -amount
            if txn_date is None:
                problems.append(f"Unparseable date '{m.group('date')}'")
            if not desc or _HEADING.match(desc) and value is None:
                continue
            bal_text = m.groupdict().get("bal")
            balance = parse_amount(bal_text) if bal_text else None
            raw = {"line": text, "page": line.page}
            amt_word = _amount_word(line, bal_text)
            if amt_word is not None:
                raw["amt_x0"] = amt_word
            unsigned = value is not None and mode == default_mode == "as_is" and not re.search(r"[-()]|\bCR\b", amt_text)
            rows.append(ParsedRow(
                row_index=idx, txn_date=txn_date, posted_date=posted, description=desc, amount=amount,
                balance=balance, raw=raw, problems=problems,
            ))
            if unsigned:
                unsigned_rows.append(rows[-1])
            k = _date_word_count(m)
            if line.words and k < len(line.words):
                desc_x0s.append(float(line.words[k]["x0"]))
            continuation = 0
            continue
        if _DATE_START.match(text) and len(text.split()) >= 3 and not re.search(r"\b(?:to|through|thru)\b", text, re.I) \
                and not re.search(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*$", text):
            # Looks like a transaction line but no amount could be read (typical OCR damage):
            # keep it as an invalid preview row so the user sees what was skipped.
            txn_date = parse_date(_DATE_START.match(text).group(1), default_year=default_year)
            if txn_date is not None and not re.search(r"\d{4}|\d{2}/\d{2}/\d{2}", _DATE_START.match(text).group(1)):
                txn_date = apply_year(txn_date, period, today)
            rows.append(ParsedRow(
                row_index=idx, txn_date=txn_date, posted_date=None,
                description=_DATE_START.sub("", text, count=1).strip(), amount=None, balance=None,
                raw={"line": text, "page": line.page}, problems=["Could not read an amount on this line"],
            ))
            continuation = 2
            continue
        if rows and continuation < 2 and not _DATE_START.match(text) and len(text) <= 90 \
                and not _HEADING.match(text) and parse_amount(text.split()[-1]) is None:
            ref = statistics.median(desc_x0s) if desc_x0s else line.x0
            if abs(line.x0 - ref) <= 24:
                rows[-1].description = (rows[-1].description + " " + text).strip()
                continuation += 1
                continue
        continuation = 2
    _infer_column_signs(unsigned_rows)
    return rows


def _amount_word(line, has_balance):
    """x0 of the word carrying the amount (second-to-last word when a balance follows)."""
    if not line.words:
        return None
    idx = -2 if has_balance and len(line.words) >= 2 else -1
    try:
        return float(line.words[idx]["x0"])
    except (KeyError, TypeError, ValueError):
        return None


def _infer_column_signs(rows, min_gap=40.0):
    """Bank statements often print unsigned amounts in separate Withdrawals | Deposits columns.
    When the amount x-positions form two clusters, the left cluster is money out."""
    xs = sorted((r.raw.get("amt_x0"), i) for i, r in enumerate(rows) if r.raw.get("amt_x0") is not None)
    if len(xs) < 2:
        return
    gaps = [(xs[i + 1][0] - xs[i][0], i) for i in range(len(xs) - 1)]
    gap, at = max(gaps)
    if gap < min_gap:
        return
    left_x = xs[at][0]
    for x, i in xs:
        if x <= left_x and rows[i].amount is not None and rows[i].amount > 0:
            rows[i].amount = -rows[i].amount


def lines_text(lines):
    return "\n".join(ln.text for ln in lines)
