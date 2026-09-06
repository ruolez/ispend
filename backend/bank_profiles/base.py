"""Declarative bank profile: how to recognise and read one institution's exports."""
import re
from dataclasses import dataclass, field
from typing import Callable

from importer.models import Mapping


@dataclass(frozen=True)
class CsvFormat:
    """One CSV/XLSX layout. `signature` = header cells that must all be present (lowercased)."""
    signature: tuple
    columns: dict                       # role -> tuple of header names; description may list several
    sign: str = "as_is"                 # as_is | flip | debit_credit
    date_format: str | None = None
    day_first: bool = False
    type_col: str | None = None         # header of a Debit/Credit type column
    debit_types: tuple = ()
    currency_columns: dict = field(default_factory=dict)   # {"CAD": "cad$", "USD": "usd$"}
    label: str = ""


@dataclass(frozen=True)
class BankProfile:
    key: str
    label: str
    country: str
    csv_formats: tuple = ()
    csv_headerless: Callable | None = None      # rows -> (score, Mapping | None)
    pdf_markers: tuple = ()
    pdf_line: re.Pattern | None = None
    pdf_sign: str = "as_is"                     # as_is | flip
    pdf_sections: tuple = ()                    # ((regex, 'charge'|'credit'|'as_is'), ...)
    pdf_stop: re.Pattern | None = None
    pdf_skip: re.Pattern | None = None
    date_format: str = "%m/%d/%Y"
    day_first: bool = False
    default_account_type: str = "checking"


def norm(cell):
    return re.sub(r"\s+", " ", str(cell or "").replace("﻿", "").strip().strip('"').lower())


def find_format_row(fmt, rows, limit=15):
    """(row index, score) where the signature matches, else None."""
    sig = set(fmt.signature)
    for i, row in enumerate(rows[:limit]):
        cells = {norm(c) for c in row if str(c).strip()}
        if sig <= cells:
            return i, (1.0 if cells == sig else 0.85)
    return None


def build_mapping_from_format(fmt, header_row, header_index):
    headers = [norm(c) for c in header_row]

    def col(names):
        for name in names or ():
            if name in headers:
                return headers.index(name)
        return None

    m = Mapping(has_header=True, skip_rows=header_index, sign=fmt.sign,
                date_format=fmt.date_format, day_first=fmt.day_first)
    m.date = col(fmt.columns.get("date"))
    m.posted_date = col(fmt.columns.get("posted_date"))
    m.description = [i for i in (col((n,)) for n in fmt.columns.get("description", ())) if i is not None]
    m.amount = col(fmt.columns.get("amount"))
    m.debit = col(fmt.columns.get("debit"))
    m.credit = col(fmt.columns.get("credit"))
    m.balance = col(fmt.columns.get("balance"))
    if fmt.type_col:
        m.type_col = col((fmt.type_col,))
        m.debit_types = list(fmt.debit_types)
    for cur, name in fmt.currency_columns.items():
        idx = col((name,))
        if idx is not None:
            m.currency_columns[cur] = idx
    if m.amount is None and m.currency_columns:
        m.amount = None
    return m


MONEY_RE = r"\(?-?\$?\s?(?:[\d,]*\d)?\.\d{2}\)?(?:\s?CR)?-?"  # ".01" penny deposits count too
DATE_RE = r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
DATE_TXT_RE = r"[A-Z][a-z]{2}\.? \d{1,2}(?:,? \d{4})?"

GENERIC_LINE = re.compile(
    rf"^(?P<date>{DATE_RE}|{DATE_TXT_RE})\s+(?:(?P<post>{DATE_RE}|{DATE_TXT_RE})\s+)?"
    rf"(?:[\'\"*\u2022\u00b7\u2019]\s+)?"  # some banks print a marker glyph between date and description
    rf"(?P<desc>.+?)\s+(?P<amt>{MONEY_RE})(?:\s+(?P<bal>{MONEY_RE}))?\s*$"
)
GENERIC_SKIP = re.compile(
    r"^(total|totals|subtotal|previous balance|new balance|minimum payment|payment due|statement balance|"
    r"balance forward|beginning balance|ending balance|opening balance|closing balance|interest charged|fees charged|"
    r"beginning totals|ending totals|totals for|daily balance|daily ending balance|"
    r"total fees|total interest|year-to-date|page \d+)",
    re.I,
)
