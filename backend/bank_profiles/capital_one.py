import re

from bank_profiles.base import BankProfile, CsvFormat

PROFILE = BankProfile(
    key="capital_one", label="Capital One", country="US",
    csv_formats=(
        CsvFormat(
            label="Capital One credit card",
            signature=("transaction date", "posted date", "card no.", "description", "category", "debit", "credit"),
            columns={"date": ("transaction date",), "posted_date": ("posted date",), "description": ("description",),
                     "debit": ("debit",), "credit": ("credit",)},
            sign="debit_credit",
        ),
        CsvFormat(
            label="Capital One 360",
            signature=("account number", "transaction date", "transaction amount", "transaction type",
                       "transaction description", "balance"),
            columns={"date": ("transaction date",), "description": ("transaction description",),
                     "amount": ("transaction amount",), "balance": ("balance",)},
            sign="as_is", type_col="transaction type", debit_types=("debit",),
        ),
    ),
    pdf_markers=("Capital One", "capitalone.com"),
    pdf_line=re.compile(r"^(?P<date>[A-Z][a-z]{2} \d{1,2})\s+(?:(?P<post>[A-Z][a-z]{2} \d{1,2})\s+)?(?P<desc>.+?)\s+(?P<amt>-?\$?[\d,]+\.\d{2})\s*$"),
    pdf_sign="flip",
    pdf_sections=(
        (re.compile(r"^Payments, Credits and Adjustments", re.I), "credit"),
        (re.compile(r"^Transactions", re.I), "charge"),
        (re.compile(r"^Fees", re.I), "charge"),
        (re.compile(r"^Interest Charged", re.I), "charge"),
    ),
    pdf_stop=re.compile(r"^(Total transactions|Total fees|Total interest|Fees charged|Interest charged|Year[- ]to[- ]date|Important information)", re.I),
    default_account_type="credit_card",
)
