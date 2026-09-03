"""Pure rule matcher. No DB access: the same functions run at import time, for
rule previews, and for retroactive application, so semantics never diverge."""
import re
from decimal import Decimal, InvalidOperation

MATCH_TYPES = ("contains", "starts_with", "equals", "regex")
MATCH_FIELDS = ("description_clean", "description_raw", "merchant_key")


def _decimal(value, name):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a number")


def _int_or_none(value, name):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")


def validate(payload, require_target=True):
    """Request body -> clean rule dict, or ValueError with a user-facing message."""
    payload = payload or {}
    match_type = payload.get("match_type") or "contains"
    if match_type not in MATCH_TYPES:
        raise ValueError("Match type must be one of: " + ", ".join(MATCH_TYPES))
    match_field = payload.get("match_field") or "description_clean"
    if match_field not in MATCH_FIELDS:
        raise ValueError("Match field must be one of: " + ", ".join(MATCH_FIELDS))
    pattern = (payload.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("Pattern is required")
    case_sensitive = bool(payload.get("case_sensitive"))
    if match_type == "regex":
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regular expression: {e}")
    amount_min = _decimal(payload.get("amount_min"), "Minimum amount")
    amount_max = _decimal(payload.get("amount_max"), "Maximum amount")
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise ValueError("Minimum amount must not exceed maximum amount")
    account_id = _int_or_none(payload.get("account_id"), "Account")
    category_id = _int_or_none(payload.get("category_id"), "Category")
    set_transfer = bool(payload.get("set_transfer"))
    set_excluded = bool(payload.get("set_excluded"))
    if require_target and category_id is None and not (set_transfer or set_excluded):
        raise ValueError("Choose a category, or mark the rule as transfer/excluded")
    name = (payload.get("name") or "").strip() or f"{match_type.replace('_', ' ')} “{pattern}”"
    try:
        priority = int(payload.get("priority", 100))
    except (TypeError, ValueError):
        raise ValueError("Priority must be an integer")
    return {
        "name": name[:200],
        "match_type": match_type,
        "match_field": match_field,
        "pattern": pattern,
        "case_sensitive": case_sensitive,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "account_id": account_id,
        "category_id": category_id,
        "set_transfer": set_transfer,
        "set_excluded": set_excluded,
        "priority": priority,
        "is_active": bool(payload.get("is_active", True)),
    }


def compile_rule(rule):
    """Attach the compiled regex / normalized pattern so matches() is cheap in loops."""
    rule = dict(rule)
    pattern = rule.get("pattern") or ""
    flags = 0 if rule.get("case_sensitive") else re.IGNORECASE
    if rule.get("match_type") == "regex":
        try:
            rule["_regex"] = re.compile(pattern, flags)
        except re.error:
            rule["_regex"] = None
    else:
        rule["_regex"] = None
        rule["_needle"] = pattern if rule.get("case_sensitive") else pattern.lower()
    return rule


def _to_decimal(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def matches(rule, txn):
    if not rule.get("is_active", True):
        return False
    if rule.get("account_id") and txn.get("account_id") != rule["account_id"]:
        return False
    amount_min = _to_decimal(rule.get("amount_min"))
    amount_max = _to_decimal(rule.get("amount_max"))
    if amount_min is not None or amount_max is not None:
        amount = _to_decimal(txn.get("amount"))
        if amount is None:
            return False
        if amount_min is not None and amount < amount_min:
            return False
        if amount_max is not None and amount > amount_max:
            return False
    text = txn.get(rule.get("match_field") or "description_clean") or ""
    match_type = rule.get("match_type") or "contains"
    if match_type == "regex":
        regex = rule.get("_regex")
        if regex is None:
            try:
                regex = re.compile(rule.get("pattern") or "", 0 if rule.get("case_sensitive") else re.IGNORECASE)
            except re.error:
                return False
        return regex.search(text) is not None
    needle = rule.get("_needle")
    if needle is None:
        needle = rule.get("pattern") or ""
        if not rule.get("case_sensitive"):
            needle = needle.lower()
    hay = text if rule.get("case_sensitive") else text.lower()
    if match_type == "contains":
        return needle in hay
    if match_type == "starts_with":
        return hay.lstrip().startswith(needle)
    if match_type == "equals":
        return hay.strip() == needle.strip()
    return False


def first_match(rules, txn):
    for rule in rules:
        if matches(rule, txn):
            return rule
    return None


def count_and_sample(rule, txns, sample_n=20):
    rule = compile_rule(rule)
    count = 0
    sample = []
    for txn in txns:
        if matches(rule, txn):
            count += 1
            if len(sample) < sample_n:
                sample.append(txn)
    return count, sample
