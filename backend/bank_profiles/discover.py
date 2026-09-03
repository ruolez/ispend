import re

from bank_profiles.base import BankProfile, CsvFormat

PROFILE = BankProfile(
    key="discover", label="Discover", country="US",
    csv_formats=(
        CsvFormat(
            label="Discover card",
            signature=("trans. date", "post date", "description", "amount", "category"),
            columns={"date": ("trans. date",), "posted_date": ("post date",), "description": ("description",),
                     "amount": ("amount",)},
            sign="flip",
        ),
    ),
    pdf_markers=("Discover", "discover.com", "Cashback Bonus"),
    pdf_line=re.compile(r"^(?P<date>\d{2}/\d{2}/\d{2,4})\s+(?P<desc>.+?)\s+(?P<amt>-?\$?[\d,]+\.\d{2})\s*$"),
    pdf_sign="flip",
    pdf_sections=(
        (re.compile(r"^Payments and Credits", re.I), "credit"),
        (re.compile(r"^(Purchases|Transactions)", re.I), "charge"),
        (re.compile(r"^(Fees|Interest Charged)", re.I), "charge"),
    ),
    default_account_type="credit_card",
)
