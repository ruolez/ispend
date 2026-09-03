import re

from bank_profiles.base import BankProfile, CsvFormat

PROFILE = BankProfile(
    key="citi", label="Citi", country="US",
    csv_formats=(
        CsvFormat(
            label="Citi card",
            signature=("status", "date", "description", "debit", "credit"),
            columns={"date": ("date",), "description": ("description",), "debit": ("debit",), "credit": ("credit",)},
            sign="debit_credit",
        ),
    ),
    pdf_markers=("Citibank", "citi.com", "Citi"),
    pdf_line=re.compile(r"^(?P<date>\d{2}/\d{2})\s+(?P<desc>.+?)\s+(?P<amt>-?\$?[\d,]+\.\d{2})\s*$"),
    pdf_sign="flip",
    pdf_sections=(
        (re.compile(r"^Payments, Credits and Adjustments", re.I), "credit"),
        (re.compile(r"^(Standard Purchases|Purchases)", re.I), "charge"),
        (re.compile(r"^Fees Charged", re.I), "charge"),
        (re.compile(r"^Interest Charged", re.I), "charge"),
    ),
    default_account_type="credit_card",
)
