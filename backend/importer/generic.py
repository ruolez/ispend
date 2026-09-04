"""Header/column heuristics for statements from unknown banks. Pure."""
import re
from datetime import date

from importer.amounts import parse_amount
from importer.dates import DATE_FORMATS, parse_date
from importer.models import Mapping

HEADER_HINTS = {
    "date": ["transaction date", "trans date", "trans. date", "date", "posting date", "date posted",
             "posted date", "post date", "transaction_date", "txn date", "value date", "booking date"],
    "posted_date": ["post date", "posted date", "posting date", "date posted", "settlement date"],
    "description": ["description", "payee", "merchant", "memo", "details", "narrative", "transaction description",
                    "description 1", "description 2", "name", "transaction", "particulars", "reference"],
    "amount": ["amount", "transaction amount", "amt", "cad$", "usd$", "value", "amount (usd)", "amount (cad)"],
    "debit": ["debit", "withdrawal", "withdrawals", "money out", "charge", "debit amount", "withdrawal amount", "paid out"],
    "credit": ["credit", "deposit", "deposits", "money in", "payment", "credit amount", "deposit amount", "paid in"],
    "balance": ["balance", "running bal.", "running balance", "running bal", "ending balance", "closing balance"],
    "type": ["type", "transaction type", "details"],
}
_ALL_HINTS = {h for hints in HEADER_HINTS.values() for h in hints}


def norm_header(cell):
    return re.sub(r"\s+", " ", str(cell or "").replace("﻿", "").strip().strip('"').lower())


def find_header_row(rows, limit=15):
    """Index of the first row (within limit) with >= 2 cells matching header hints."""
    for i, row in enumerate(rows[:limit]):
        cells = [norm_header(c) for c in row]
        hits = sum(1 for c in cells if c in _ALL_HINTS or any(c.startswith(h) for h in ("date", "description", "amount")))
        if hits >= 2:
            return i
    return None


def _is_date_like(cell):
    return parse_date(cell) is not None and not re.fullmatch(r"-?[\d,]+\.\d{2}", str(cell).strip())


def _is_money_like(cell):
    s = str(cell or "").strip()
    return bool(s) and parse_amount(s) is not None and not re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}", s) \
        and not re.match(r"^\d{1,2}/\d{1,2}/\d{2,4}$", s) and not re.fullmatch(r"\d{8}", s)


def column_profile(rows, max_rows=25):
    """Per-column stats: fraction of date-like, money-like cells and mean text length."""
    width = max((len(r) for r in rows[:max_rows]), default=0)
    stats = []
    for c in range(width):
        cells = [r[c] if c < len(r) else "" for r in rows[:max_rows]]
        non_empty = [x for x in cells if str(x).strip()]
        n = len(non_empty) or 1
        stats.append({
            "date": sum(1 for x in non_empty if _is_date_like(x)) / n,
            "money": sum(1 for x in non_empty if _is_money_like(x)) / n,
            "text_len": sum(len(str(x)) for x in non_empty) / n,
            "filled": len(non_empty) / (len(cells) or 1),
            "star": sum(1 for x in non_empty if str(x).strip() == "*") / n,
        })
    return stats


def elect_date_format(cells, day_first_hint=False):
    """Pick the strptime format that parses every date cell; disambiguate MM/DD vs DD/MM."""
    cells = [str(c).strip() for c in cells if str(c or "").strip()]
    if not cells:
        return None, False
    candidates = []
    for fmt in DATE_FORMATS:
        ok = 0
        for c in cells:
            try:
                from datetime import datetime
                datetime.strptime(c, fmt)
                ok += 1
            except ValueError:
                break
        if ok == len(cells):
            candidates.append(fmt)
    if not candidates:
        return None, day_first_hint
    if "%m/%d/%Y" in candidates and "%d/%m/%Y" in candidates:
        return ("%d/%m/%Y", True) if day_first_hint else ("%m/%d/%Y", False)
    if "%m/%d/%y" in candidates and "%d/%m/%y" in candidates:
        return ("%d/%m/%y", True) if day_first_hint else ("%m/%d/%y", False)
    fmt = candidates[0]
    return fmt, fmt.startswith("%d")


def _match_by_hints(headers):
    """role -> column index (description may be many)."""
    found = {}
    desc = []
    for role, hints in HEADER_HINTS.items():
        for idx, h in enumerate(headers):
            if not h:
                continue
            exact = h in hints
            fuzzy = any(h.startswith(hint) or hint == h.rstrip(":") for hint in hints)
            if role == "description":
                if exact or fuzzy:
                    if idx not in desc and idx not in found.values():
                        desc.append(idx)
                continue
            if (exact or fuzzy) and role not in found and idx not in found.values() and idx not in desc:
                if role == "date" and h in HEADER_HINTS["posted_date"] and any(
                        x in headers for x in ("transaction date", "trans date", "trans. date", "date")):
                    continue
                found[role] = idx
    if desc:
        found["description"] = desc
    return found


def guess_mapping(rows):
    """Best-effort Mapping for an unknown CSV/XLSX. Returns (mapping, warnings)."""
    warnings = []
    m = Mapping()
    header_idx = find_header_row(rows)
    if header_idx is not None:
        headers = [norm_header(c) for c in rows[header_idx]]
        roles = _match_by_hints(headers)
        m.has_header = True
        m.skip_rows = header_idx
        m.date = roles.get("date")
        m.posted_date = roles.get("posted_date")
        m.description = roles.get("description", [])
        m.amount = roles.get("amount")
        m.debit = roles.get("debit")
        m.credit = roles.get("credit")
        m.balance = roles.get("balance")
        if roles.get("type") is not None and m.amount is not None:
            data_rows = rows[header_idx + 1:header_idx + 40]
            types = {str(r[roles["type"]]).strip().lower() for r in data_rows if roles["type"] < len(r)}
            if types & {"debit", "credit", "dr", "cr"}:
                m.type_col = roles["type"]
                m.debit_types = ["debit", "dr"]
        if m.date is None and m.posted_date is not None:
            m.date, m.posted_date = m.posted_date, None
        data_rows = rows[header_idx + 1:]
    else:
        m.has_header = False
        m.skip_rows = 0
        data_rows = rows
        stats = column_profile(rows)
        date_cols = sorted((i for i, s in enumerate(stats) if s["date"] >= 0.8), key=lambda i: -stats[i]["date"])
        money_cols = [i for i, s in enumerate(stats) if s["money"] >= 0.6 and i not in date_cols]
        text_cols = [i for i, s in enumerate(stats) if i not in date_cols and i not in money_cols and s["text_len"] >= 4]
        if date_cols:
            m.date = date_cols[0]
            if len(date_cols) > 1:
                m.posted_date = date_cols[1]
        if len(stats) == 5 and len(money_cols) >= 1 and stats[2]["star"] >= 0.8:
            m.amount, m.description = 1, [4]
        elif len(money_cols) >= 3:
            m.debit, m.credit, m.balance = money_cols[0], money_cols[1], money_cols[2]
            m.sign = "debit_credit"
        elif len(money_cols) == 2:
            filled = [stats[i]["filled"] for i in money_cols]
            if all(f >= 0.95 for f in filled):
                m.amount, m.balance = money_cols
            else:
                m.debit, m.credit = money_cols
                m.sign = "debit_credit"
        elif len(money_cols) == 1:
            m.amount = money_cols[0]
        if not m.description and text_cols:
            m.description = [max(text_cols, key=lambda i: stats[i]["text_len"])]
        if m.date is None or (m.amount is None and m.debit is None):
            warnings.append("Could not detect the date/amount columns; please map them manually.")
    if m.debit is not None and m.credit is not None and m.amount is None:
        m.sign = "debit_credit"
    if m.date is not None:
        date_cells = [r[m.date] for r in data_rows[:60] if m.date < len(r)]
        fmt, day_first = elect_date_format(date_cells)
        m.date_format, m.day_first = fmt, day_first
        if fmt is None and date_cells:
            warnings.append("Dates use an unrecognised format; check the date column and format.")
    if m.sign != "debit_credit" and m.amount is not None:
        vals = [parse_amount(r[m.amount]) for r in data_rows[:200] if m.amount < len(r)]
        vals = [v for v in vals if v is not None and v != 0]
        if len(vals) >= 5 and sum(1 for v in vals if v > 0) / len(vals) >= 0.8:
            warnings.append("Most amounts are positive; if these are charges, flip the sign in the preview.")
    if not m.description:
        warnings.append("No description column detected.")
    return m, warnings


def looks_inverted(amounts, account_type=None, min_rows=5, threshold=0.8):
    """True when a statement's amounts read as mostly money-in although the account is a card.

    Card exports commonly list charges as positive numbers; in our convention money out is
    negative, so such a file needs its signs flipped. Only credit-type accounts are judged:
    a savings account really can be mostly deposits.
    """
    if account_type not in ("credit_card", "line_of_credit", "loan"):
        return False
    vals = [a for a in amounts if a is not None and a != 0]
    if len(vals) < min_rows:
        return False
    return sum(1 for v in vals if v > 0) / len(vals) >= threshold
