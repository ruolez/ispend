import re

from bank_profiles.base import BankProfile
from importer.models import Mapping


def headerless(rows):
    """Wells Fargo export: date, amount, '*', '', description — no header row."""
    sample = rows[:20]
    if not sample:
        return 0.0, None
    from importer.amounts import parse_amount
    from importer.dates import parse_date

    ok = 0
    for r in sample:
        if len(r) == 5 and parse_date(r[0]) and parse_amount(r[1]) is not None and r[2].strip() == "*" and not r[3].strip():
            ok += 1
    score = ok / len(sample)
    if score < 0.8:
        return 0.0, None
    return score, Mapping(has_header=False, date=0, amount=1, description=[4], sign="as_is", date_format="%m/%d/%Y")


PROFILE = BankProfile(
    key="wells_fargo", label="Wells Fargo", country="US",
    csv_headerless=headerless,
    pdf_markers=("Wells Fargo", "wellsfargo.com"),
    pdf_line=re.compile(r"^(?P<date>\d{1,2}/\d{1,2})\s+(?P<desc>.+?)\s+(?P<amt>[\d,]+\.\d{2})(?:\s+(?P<bal>[\d,]+\.\d{2}))?\s*$"),
    pdf_sign="as_is",
    pdf_sections=(
        (re.compile(r"^Deposits", re.I), "credit"),
        (re.compile(r"^(Withdrawals|Checks paid|Fees|Monthly service fee)", re.I), "charge"),
        (re.compile(r"^Transaction history", re.I), "as_is"),
    ),
    default_account_type="checking",
)
