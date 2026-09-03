import re

from bank_profiles.base import BankProfile
from importer.models import Mapping


def headerless(rows):
    """TD Canada Trust export: date, description, debit, credit, balance — no header."""
    sample = rows[:20]
    if not sample:
        return 0.0, None
    from importer.amounts import parse_amount
    from importer.dates import parse_date

    ok = 0
    for r in sample:
        if len(r) == 5 and parse_date(r[0]) and r[1].strip() and r[2].strip() != "*" \
                and (parse_amount(r[2]) is not None or parse_amount(r[3]) is not None) \
                and parse_amount(r[4]) is not None and not parse_date(r[1]) and not parse_amount(r[1]):
            ok += 1
    score = ok / len(sample)
    if score < 0.8:
        return 0.0, None
    return score, Mapping(has_header=False, date=0, description=[1], debit=2, credit=3, balance=4,
                          sign="debit_credit", date_format="%m/%d/%Y")


PROFILE = BankProfile(
    key="td", label="TD Canada Trust", country="CA",
    csv_headerless=headerless,
    pdf_markers=("TD Canada Trust", "TD Bank", "tdcanadatrust.com", "td.com"),
    pdf_line=re.compile(r"^(?P<date>[A-Z]{3}\d{1,2}|[A-Z][a-z]{2} \d{1,2}|\d{2}/\d{2})\s+(?P<desc>.+?)\s+(?P<amt>[\d,]+\.\d{2})(?:\s+(?P<bal>[\d,]+\.\d{2}))?\s*$"),
    pdf_sign="as_is",
    pdf_sections=(
        (re.compile(r"^(Deposits|Credits)", re.I), "credit"),
        (re.compile(r"^(Withdrawals|Debits|Cheques)", re.I), "charge"),
        (re.compile(r"^(Payments|Payments and Credits)", re.I), "credit"),
        (re.compile(r"^(Purchases|Transactions)", re.I), "charge"),
    ),
    default_account_type="checking",
)
