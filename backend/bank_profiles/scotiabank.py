import re

from bank_profiles.base import BankProfile, CsvFormat
from bank_profiles.wells_fargo import headerless as _wf_headerless

# Scotiabank's headerless chequing export shares the Wells Fargo shape (date, amount, *, '', description);
# detection lands on wells_fargo and the user can relabel the statement as Scotiabank in the preview.
PROFILE = BankProfile(
    key="scotiabank", label="Scotiabank", country="CA",
    csv_headerless=_wf_headerless,
    csv_formats=(
        CsvFormat(
            label="Scotiabank (with header)",
            signature=("date", "amount", "description", "sub-description", "type of transaction"),
            columns={"date": ("date",), "description": ("description", "sub-description"), "amount": ("amount",)},
            sign="as_is",
        ),
    ),
    pdf_markers=("Scotiabank", "scotiabank.com", "Bank of Nova Scotia"),
    pdf_line=re.compile(r"^(?P<date>[A-Z][a-z]{2} \d{1,2}|\d{1,2} [A-Z][a-z]{2})\s+(?P<desc>.+?)\s+(?P<amt>[\d,]+\.\d{2})(?:\s+(?P<bal>[\d,]+\.\d{2}))?\s*$"),
    pdf_sign="as_is",
    pdf_sections=(
        (re.compile(r"^(Deposits|Credits)", re.I), "credit"),
        (re.compile(r"^(Withdrawals|Debits)", re.I), "charge"),
    ),
    pdf_stop=re.compile(r"^(Closing balance|Total (deposits|withdrawals)|Important information)", re.I),
    default_account_type="checking",
)
