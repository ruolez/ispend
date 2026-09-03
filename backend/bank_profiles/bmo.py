import re

from bank_profiles.base import BankProfile, CsvFormat

PROFILE = BankProfile(
    key="bmo", label="BMO Bank of Montreal", country="CA",
    csv_formats=(
        CsvFormat(
            label="BMO chequing",
            signature=("first bank card", "transaction type", "date posted", "transaction amount", "description"),
            columns={"date": ("date posted",), "description": ("description",), "amount": ("transaction amount",)},
            sign="as_is", date_format="%Y%m%d",
        ),
        CsvFormat(
            label="BMO credit card",
            signature=("item #", "card #", "transaction date", "posting date", "transaction amount", "description"),
            columns={"date": ("transaction date",), "posted_date": ("posting date",), "description": ("description",),
                     "amount": ("transaction amount",)},
            sign="flip", date_format="%Y%m%d",
        ),
    ),
    pdf_markers=("Bank of Montreal", "BMO", "bmo.com"),
    pdf_line=re.compile(r"^(?P<date>[A-Z][a-z]{2}\.? \d{1,2})\s+(?:(?P<post>[A-Z][a-z]{2}\.? \d{1,2})\s+)?(?P<desc>.+?)\s+(?P<amt>[\d,]+\.\d{2}(?:\s?CR)?)(?:\s+(?P<bal>[\d,]+\.\d{2}))?\s*$"),
    pdf_sign="as_is",
    pdf_sections=(
        (re.compile(r"^(Deposits|Credits)", re.I), "credit"),
        (re.compile(r"^(Withdrawals|Debits)", re.I), "charge"),
        (re.compile(r"^Transactions since your last statement", re.I), "as_is"),
    ),
    default_account_type="checking",
)
