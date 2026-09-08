"""Pure rules for split transactions: a parent's amount divided over >= 2 category lines."""
from decimal import Decimal, InvalidOperation

MAX_LINES = 20
NOTE_MAX = 200
CENT = Decimal("0.01")


def _money(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, ValueError):
        return None
    return out if out.is_finite() else None


def fmt(value):
    return f"{Decimal(value).quantize(CENT):,.2f}"


def remaining(parent_amount, lines):
    """What is still unallocated: parent minus the sum of the lines (2 places)."""
    parent = _money(parent_amount) or Decimal("0")
    total = sum((_money(ln.get("amount")) or Decimal("0") for ln in lines if isinstance(ln, dict)), Decimal("0"))
    return (parent - total).quantize(CENT)


def validate_splits(parent_amount, lines):
    """(clean_lines, error). clean_lines carry category_id (int), amount (Decimal), note (str|None).
    Errors are user-facing sentences; the first problem wins."""
    parent = _money(parent_amount)
    if parent is None or parent == 0:
        return None, "Only transactions with an amount can be split"
    if not isinstance(lines, list):
        return None, "lines must be a list"
    if len(lines) < 2:
        return None, "A split needs at least two lines"
    if len(lines) > MAX_LINES:
        return None, f"A split can have at most {MAX_LINES} lines"
    clean = []
    for i, ln in enumerate(lines, 1):
        if not isinstance(ln, dict):
            return None, f"Line {i} is not an object"
        cat = ln.get("category_id")
        if isinstance(cat, bool) or cat in (None, ""):
            return None, f"Line {i} needs a category"
        try:
            cat = int(cat)
        except (TypeError, ValueError):
            return None, f"Line {i} needs a category"
        amount = _money(ln.get("amount"))
        if amount is None:
            return None, f"Line {i} needs an amount"
        if amount == 0:
            return None, f"Line {i} cannot be zero"
        if (amount < 0) != (parent < 0):
            return None, f"Line {i} must be {'negative' if parent < 0 else 'positive'} like the transaction"
        note = ln.get("note")
        note = " ".join(str(note).split()) if note not in (None, "") else None
        if note and len(note) > NOTE_MAX:
            return None, f"Line {i} note must be at most {NOTE_MAX} characters"
        clean.append({"category_id": cat, "amount": amount, "note": note or None})
    total = sum((c["amount"] for c in clean), Decimal("0"))
    if total != parent:
        left = (parent - total).quantize(CENT)
        return None, f"Lines add up to {fmt(total)} but the transaction is {fmt(parent)} ({fmt(left)} left)"
    return clean, None


def summary_text(lines, names):
    """'Groceries 40.00 · Household 12.50' for chips/CSV; names maps category_id -> name."""
    parts = []
    for ln in lines:
        name = names.get(ln.get("category_id")) or "Uncategorized"
        parts.append(f"{name} {fmt(abs(Decimal(str(ln.get('amount') or 0))))}")
    return " · ".join(parts)
