import re

from bank_profiles.base import BankProfile, CsvFormat

# PNC's Activity Detail export: separate Withdrawals/Deposits columns, "$1,234.56" strings, newest first.
# Newer exports add a Category column; the 2024+ activity download is a single signed Amount column.
PROFILE = BankProfile(
    key="pnc", label="PNC", country="US",
    csv_formats=(
        CsvFormat(
            label="PNC checking / savings",
            signature=("date", "description", "withdrawals", "deposits", "balance"),
            columns={"date": ("date",), "description": ("description",), "debit": ("withdrawals",),
                     "credit": ("deposits",), "balance": ("balance",)},
            sign="debit_credit",
        ),
        CsvFormat(
            label="PNC checking / savings (with category)",
            signature=("date", "description", "withdrawals", "deposits", "category", "balance"),
            columns={"date": ("date",), "description": ("description",), "debit": ("withdrawals",),
                     "credit": ("deposits",), "balance": ("balance",)},
            sign="debit_credit",
        ),
        CsvFormat(
            label="PNC activity (signed amount)",
            signature=("transaction date", "transaction description", "amount"),
            columns={"date": ("transaction date",), "description": ("transaction description",),
                     "amount": ("amount",), "balance": ("balance",)},
            sign="as_is",
        ),
    ),
    pdf_markers=("PNC Bank", "pnc.com", "Virtual Wallet"),
    # Checking statements print "MM/DD amount description"; card statements end the line with the amount
    # (payments carry a trailing minus) and fall through to the generic date-description-amount pattern.
    pdf_line=re.compile(r"^(?P<date>\d{2}/\d{2})\s+(?P<amt>[\d,]+\.\d{2}-?)\s+(?P<desc>.+?)\s*$"),
    pdf_sign="flip",
    pdf_sections=(
        (re.compile(r"^Deposits and Other Additions", re.I), "credit"),
        (re.compile(r"^(Checks and Substitute Checks|Banking/(Debit|Check) Card Withdrawals and Purchases|"
                    r"Online and Electronic Banking Deductions|Other Deductions|Fees and Service Charges|"
                    r"ATM Withdrawals)", re.I), "charge"),
        (re.compile(r"^(Transactions and Fees|Purchases and Adjustments|Fees Charged|Interest Charged)", re.I), "as_is"),
        (re.compile(r"^(Payments and Other Credits|Payments Received)", re.I), "credit"),
    ),
    pdf_skip=re.compile(r"^(There (was|were) \d+|Date Amount Description|Date Balance)", re.I),
    pdf_stop=re.compile(r"^(Daily Balance Detail|\d{4} totals year[- ]to[- ]date|Total fees charged|Total interest charged|"
                        r"Interest Charge Calculation|Important account information)", re.I),
    default_account_type="checking",
)
