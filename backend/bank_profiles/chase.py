import re

from bank_profiles.base import BankProfile, CsvFormat

PROFILE = BankProfile(
    key="chase", label="Chase", country="US",
    csv_formats=(
        CsvFormat(
            label="Chase checking",
            signature=("details", "posting date", "description", "amount", "type", "balance"),
            columns={"date": ("posting date",), "description": ("description",), "amount": ("amount",),
                     "balance": ("balance",)},
            sign="as_is",
        ),
        CsvFormat(
            label="Chase credit card",
            signature=("transaction date", "post date", "description", "category", "type", "amount"),
            columns={"date": ("transaction date",), "posted_date": ("post date",), "description": ("description",),
                     "amount": ("amount",)},
            sign="as_is",
        ),
    ),
    pdf_markers=("JPMorgan Chase", "chase.com", "Chase"),
    pdf_line=re.compile(r"^(?P<date>\d{2}/\d{2})\s+(?P<desc>.+?)\s+(?P<amt>-?[\d,]+\.\d{2})\s*$"),
    pdf_sign="flip",
    pdf_sections=(
        (re.compile(r"^PAYMENTS AND OTHER CREDITS", re.I), "credit"),
        (re.compile(r"^PURCHASE", re.I), "charge"),
        (re.compile(r"^FEES CHARGED", re.I), "charge"),
        (re.compile(r"^INTEREST CHARGED", re.I), "charge"),
        (re.compile(r"^(DEPOSITS AND ADDITIONS)", re.I), "credit"),
        (re.compile(r"^(ATM & DEBIT CARD WITHDRAWALS|ELECTRONIC WITHDRAWALS|CHECKS PAID|FEES)", re.I), "charge"),
    ),
    pdf_stop=re.compile(r"^(TOTAL INTEREST CHARGED IN \d{4}|Year-to-date totals)", re.I),
    default_account_type="credit_card",
)
