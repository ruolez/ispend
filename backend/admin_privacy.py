"""What an administrator may see of a person's own activity.

iSpend's rule is counts, not contents: an admin can tell that someone imported a statement or
renamed a category, never what the statement was called, what the category is, which merchant or
how much. Audit rows written by users carry some of that in `detail` (filenames, account and tag
names, merchant keys, budget amounts), so everything shown in the admin passes through here.

Allow-list by shape, not by action: a new audit call that adds a free-text field is hidden by
default instead of leaking until somebody remembers to update a deny list.
"""

# Strings are only kept for keys whose values are enums, dates or field names — never user text.
STRING_KEYS = {
    "action", "kind", "status", "plan", "template", "month", "from", "to", "source", "to_status",
    "error_code", "cached",
}
# Numbers are ids and counts, except these, which are money.
MONEY_KEYS = {"amount", "balance", "total", "sum", "value", "price"}
# Lists of field or setting names ("which columns changed"), never values.
NAME_LIST_KEYS = {"fields", "keys"}
MAX_STRING = 40


def _count_map(value):
    return (isinstance(value, dict) and len(value) <= 20
            and all(isinstance(k, str) and len(k) <= MAX_STRING and not isinstance(v, bool)
                    and isinstance(v, (int, float)) for k, v in value.items()))


def redact_detail(detail):
    """(clean detail, hidden count). Non-dict details pass through only when empty."""
    if detail is None:
        return None, 0
    if not isinstance(detail, dict):
        return None, 1
    out, hidden = {}, 0
    for key, value in detail.items():
        if value is None or isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            if key in MONEY_KEYS:
                hidden += 1
            else:
                out[key] = value
        elif isinstance(value, str):
            if key in STRING_KEYS and len(value) <= MAX_STRING:
                out[key] = value
            else:
                hidden += 1
        elif key in NAME_LIST_KEYS and isinstance(value, list) and all(
                isinstance(v, str) and len(v) <= MAX_STRING for v in value):
            out[key] = value
        elif _count_map(value):
            out[key] = value
        else:
            hidden += 1
    return out, hidden


def redact_row(row):
    """An audit row as the admin sees it. Admin actions are shown whole: they are the admin's own
    record, and the people they name are the admin's to manage."""
    if row.get("by_admin"):
        return row
    detail, hidden = redact_detail(row.get("detail"))
    out = {**row, "detail": detail}
    if hidden:
        out["hidden_fields"] = hidden
    return out
