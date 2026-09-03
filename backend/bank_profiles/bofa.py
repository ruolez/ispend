import re

from bank_profiles.base import BankProfile, CsvFormat

PROFILE = BankProfile(
    key="bofa", label="Bank of America", country="US",
    csv_formats=(
        CsvFormat(
            label="Bank of America checking",
            signature=("date", "description", "amount", "running bal."),
            columns={"date": ("date",), "description": ("description",), "amount": ("amount",),
                     "balance": ("running bal.",)},
            sign="as_is",
        ),
        CsvFormat(
            label="Bank of America credit card",
            signature=("posted date", "reference number", "payee", "address", "amount"),
            columns={"date": ("posted date",), "description": ("payee",), "amount": ("amount",)},
            sign="as_is",
        ),
    ),
    pdf_markers=("Bank of America", "bankofamerica.com"),
    pdf_line=re.compile(r"^(?P<date>\d{2}/\d{2}/\d{2,4})\s+(?P<desc>.+?)\s+(?P<amt>-?[\d,]+\.\d{2})\s*$"),
    pdf_sign="as_is",
    pdf_sections=(
        (re.compile(r"^Deposits and other (additions|credits)", re.I), "credit"),
        (re.compile(r"^(Withdrawals and other (subtractions|debits)|Checks|Service fees|Purchases and Adjustments)", re.I), "charge"),
        (re.compile(r"^Payments and Other Credits", re.I), "credit"),
        (re.compile(r"^(Fee Transactions|Interest Charged)", re.I), "charge"),
    ),
    default_account_type="checking",
)
