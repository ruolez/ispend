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

_DATE_START = re.compile(r"^(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|[A-Z][a-z]{2}\.? \d{1,2}|\d{1,2} [A-Z][a-z]{2}|[A-Z]{3}\d{1,2})\b")
_MARKER_TOKEN = re.compile(r"^[\'\"*\u2022\u00b7\u2019]+$")
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

def _looks_like_damaged_row(text):
    """A date-led line with a merchant-like word and some digits (a garbled amount) — not a page
    header such as "May 08, 2026 0000422304" or "05-08 04/2026 statement"."""
    rest = _DATE_START.sub("", text, count=1)
    rest = re.sub(r"^\s*,?\s*(?:\d{4})?\s*", "", rest)  # drop a trailing ", 2026" of a long date
    words = re.findall(r"[A-Za-z]{3,}", rest)
    return bool(words) and bool(re.search(r"\d", rest)) and not re.fullmatch(r"[\s\d,.-]*", rest)


def rows_to_transactions(lines, profile, period, today=None, flip_sign=False):
    """State machine over lines: section headings set the sign mode, matching lines become rows,
    indented non-matching lines extend the previous description."""
    line_re = profile.pdf_line if (profile and profile.pdf_line) else GENERIC_LINE
    default_mode = profile.pdf_sign if profile else "as_is"
    mode = default_mode
    default_year = period[1].year if period else (today or date.today()).year
    rows, desc_x0s, continuation, unsigned_rows, date_x0s = [], [], 2, [], []
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
            desc_tokens = m.group("desc").split()
            while desc_tokens and _MARKER_TOKEN.match(desc_tokens[0]):
                desc_tokens.pop(0)
            desc = " ".join(desc_tokens).strip()
            if GENERIC_SKIP.match(desc):
                continuation = 2
                continue  # "03-08 Beginning balance $1,240.61" is a balance line, not a transaction
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
            if bal_text and line.words:
                try:
                    raw["bal_x0"] = float(line.words[-1]["x0"])
                except (KeyError, TypeError, ValueError):
                    pass
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
            date_x0s.append(float(line.x0))
            continuation = 0
            continue
        if _DATE_START.match(text) and len(text.split()) >= 3 and not re.search(r"\b(?:to|through|thru)\b", text, re.I) \
                and not re.search(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?\s*$", text) and _looks_like_damaged_row(text):
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
        # An all-caps line is a section heading only at the left margin; indented ones are detail lines.
        indented = bool(date_x0s) and line.x0 - statistics.median(date_x0s) > 8
        if rows and continuation < 2 and not _DATE_START.match(text) and len(text) <= 90 \
                and (indented or not _HEADING.match(text)) and parse_amount(text.split()[-1]) is None:
            ref = statistics.median(desc_x0s) if desc_x0s else line.x0
            # continuation lines sit at the description column or indented a little further in
            if -24 <= line.x0 - ref <= 70:
                rows[-1].description = (rows[-1].description + " " + text).strip()
                continuation += 1
                continue
        continuation = 2
    _infer_column_signs(unsigned_rows)
    reconcile_with_balances(rows)
    mark_ledger_twins(rows)
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
    When the amount x-positions on a page form two clusters, the left cluster is money out.
    Judged per page: a deposit-ticket listing on another page must not be read as a column."""
    by_page, bal_x = {}, {}
    for i, r in enumerate(rows):
        if r.raw.get("amt_x0") is not None:
            by_page.setdefault(r.raw.get("page"), []).append((r.raw["amt_x0"], i))
        if r.raw.get("bal_x0") is not None:
            bal_x.setdefault(r.raw.get("page"), []).append(r.raw["bal_x0"])
    for page, xs in by_page.items():
        # an "amount" printed where this page prints balances is a misread line, not a column
        if bal_x.get(page):
            bx = statistics.median(bal_x[page])
            xs = [(x, i) for x, i in xs if abs(x - bx) > 8]
        xs.sort()
        if len(xs) < 2:
            continue
        gaps = [(xs[i + 1][0] - xs[i][0], i) for i in range(len(xs) - 1)]
        gap, at = max(gaps)
        if gap < min_gap:
            continue
        left_x = xs[at][0]
        for x, i in xs:
            if x <= left_x and rows[i].amount is not None and rows[i].amount > 0:
                rows[i].amount = -rows[i].amount


def reconcile_with_balances(rows, tol=Decimal("0.01")):
    """Use the running balance as a cross-check on ledger statements. When most consecutive rows
    satisfy prev_balance + amount == balance, the exceptions are repaired: a flipped sign, or a
    line whose only number was really the balance (a penny deposit such as 'ACH Deposit .01')."""
    seq = [r for r in rows if r.is_valid and r.amount is not None and r.raw.get("twin_of") is None]
    if len(seq) < 4:
        return 0
    checks = ok = 0
    prev = None
    for r in seq:
        if prev is not None and r.balance is not None:
            checks += 1
            if abs(prev + r.amount - r.balance) <= tol:
                ok += 1
        if r.balance is not None:
            prev = r.balance
    if checks < 3 or ok / checks < 0.6:
        return 0
    fixed = 0
    prev = None
    for i, r in enumerate(seq):
        if prev is not None:
            if r.balance is not None and abs(prev + r.amount - r.balance) > tol:
                if abs(prev - r.amount - r.balance) <= tol:
                    r.amount = -r.amount
                    r.raw["reconciled"] = "sign"
                    fixed += 1
            elif r.balance is None:
                # the number we took as the amount may be the balance: does the next row chain from it?
                nxt = seq[i + 1] if i + 1 < len(seq) else None
                candidate = r.amount
                if nxt is not None and nxt.balance is not None and abs(candidate + nxt.amount - nxt.balance) <= tol \
                        and abs(prev + candidate - candidate) > tol:
                    implied = (candidate - prev).quantize(Decimal("0.01"))
                    if implied != 0:
                        r.balance, r.amount = candidate, implied
                        r.raw["reconciled"] = "balance"
                        fixed += 1
        if r.balance is not None:
            prev = r.balance
    return fixed


def mark_ledger_twins(rows):
    """Statements sometimes list a transaction twice: once in the running-balance ledger and again in
    a deposit/check image section without a balance. Flag the balance-less copy as a twin."""
    ledger = {}
    for r in rows:
        if r.is_valid and r.balance is not None:
            ledger.setdefault((r.txn_date, abs(r.amount)), r.row_index)
    if not ledger:
        return 0
    n = 0
    for r in rows:
        if r.is_valid and r.balance is None and (r.txn_date, abs(r.amount)) in ledger:
            r.raw["twin_of"] = ledger[(r.txn_date, abs(r.amount))]
            n += 1
    return n


def lines_text(lines):
    return "\n".join(ln.text for ln in lines)
