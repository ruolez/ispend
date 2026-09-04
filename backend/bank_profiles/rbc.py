import re

from bank_profiles.base import BankProfile, CsvFormat

PROFILE = BankProfile(
    key="rbc", label="RBC Royal Bank", country="CA",
    csv_formats=(
        CsvFormat(
            label="RBC",
            signature=("account type", "account number", "transaction date", "description 1", "cad$"),
            columns={"date": ("transaction date",), "description": ("description 1", "description 2"),
                     "balance": ()},
            sign="as_is", currency_columns={"CAD": "cad$", "USD": "usd$"},
        ),
    ),
    pdf_markers=("Royal Bank of Canada", "RBC Royal Bank", "rbc.com", "RBC"),
    pdf_line=re.compile(r"^(?P<date>\d{1,2} [A-Z][a-z]{2}|[A-Z][a-z]{2} \d{1,2})\s+(?P<desc>.+?)\s+(?P<amt>[\d,]+\.\d{2})(?:\s+(?P<bal>[\d,]+\.\d{2}))?\s*$"),
    pdf_sign="as_is",
    pdf_sections=(
        (re.compile(r"^(Deposits|Deposits & credits)", re.I), "credit"),
        (re.compile(r"^(Withdrawals|Withdrawals & debits|Cheques)", re.I), "charge"),
        (re.compile(r"^Payments & credits", re.I), "credit"),
        (re.compile(r"^(Purchases & adjustments|Purchases)", re.I), "charge"),
    ),
    pdf_stop=re.compile(r"^(Closing balance|Total (deposits|withdrawals)|Summary of your account|Important information)", re.I),
    default_account_type="checking",
)
