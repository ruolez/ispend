"""Money string -> Decimal. Pure."""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_STRIP = re.compile(r"[\s $€£,]")
_CURRENCY_WORDS = re.compile(r"(?i)\b(USD|CAD|US|CA)\b")
_TRAILING_CR = re.compile(r"(?i)(?<=[\d\s)])\s*CR\.?\s*$")
_TRAILING_DR = re.compile(r"(?i)(?<=[\d\s)])\s*DR\.?\s*$")
TWO_PLACES = Decimal("0.01")


def parse_amount(value):
    """'$1,234.56' '(12.34)' '12.34-' '45.00 CR' '-$50' -> Decimal (2 places) or None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("−", "-").replace("–", "-").replace("—", "-")
    negative = False
    credit = False
    if _TRAILING_CR.search(text):
        credit = True
        text = _TRAILING_CR.sub("", text)
    elif _TRAILING_DR.search(text):
        negative = True
        text = _TRAILING_DR.sub("", text)
    text = _CURRENCY_WORDS.sub("", text).strip()
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    text = _STRIP.sub("", text)
    if text.endswith("-"):
        negative = True
        text = text[:-1]
    elif text.endswith("+"):
        text = text[:-1]
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("-"):
        negative = True
        text = text[1:]
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if not text or not re.fullmatch(r"\d*\.?\d+|\d+\.", text):
        return None
    try:
        amount = Decimal(text).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None
    if credit:
        return abs(amount)
    return -amount if negative else amount


def looks_like_amount(value):
    return parse_amount(value) is not None
