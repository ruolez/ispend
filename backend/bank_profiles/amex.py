import re

from bank_profiles.base import BankProfile, CsvFormat

PROFILE = BankProfile(
    key="amex", label="American Express", country="US",
    csv_formats=(
        CsvFormat(
            label="Amex card",
            signature=("date", "description", "card member", "account #", "amount"),
            columns={"date": ("date",), "description": ("description",), "amount": ("amount",)},
            sign="flip",
        ),
    ),
    pdf_markers=("American Express", "americanexpress.com", "Membership Rewards"),
    pdf_line=re.compile(r"^(?P<date>\d{2}/\d{2}/\d{2,4})\*?\s+(?P<desc>.+?)\s+(?P<amt>-?\$?[\d,]+\.\d{2})\s*$"),
    pdf_sign="flip",
    pdf_sections=(
        (re.compile(r"^Payments and Credits", re.I), "credit"),
        (re.compile(r"^New Charges", re.I), "charge"),
        (re.compile(r"^Fees", re.I), "charge"),
        (re.compile(r"^Interest Charged", re.I), "charge"),
    ),
    pdf_stop=re.compile(r"^(Total Fees|Total Interest Charged|About Trailing Interest)", re.I),
    default_account_type="credit_card",
)
