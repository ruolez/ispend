"""Data classes shared by the parsers. Pure; no DB access."""
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal

SIGN_MODES = ("as_is", "flip", "debit_credit")


@dataclass
class Mapping:
    """How to read a tabular (CSV/XLSX) statement. Persisted as statements.mapping."""
    has_header: bool = True
    skip_rows: int = 0
    delimiter: str = ","
    date: int | None = None
    posted_date: int | None = None
    description: list[int] = field(default_factory=list)
    amount: int | None = None
    debit: int | None = None
    credit: int | None = None
    balance: int | None = None
    date_format: str | None = None
    day_first: bool = False
    sign: str = "as_is"
    flip_sign: bool = False
    currency_columns: dict = field(default_factory=dict)
    type_col: int | None = None
    debit_types: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        data = dict(data or {})
        m = cls()
        for key in m.__dataclass_fields__:
            if key in data and data[key] is not None:
                setattr(m, key, data[key])
        m.description = [int(i) for i in (m.description or []) if i is not None and str(i) != ""]
        for key in ("date", "posted_date", "amount", "debit", "credit", "balance", "type_col", "skip_rows"):
            v = getattr(m, key)
            if v == "" or v is None:
                setattr(m, key, None if key != "skip_rows" else 0)
            else:
                setattr(m, key, int(v))
        if m.sign not in SIGN_MODES:
            m.sign = "as_is"
        if m.amount is None and (m.debit is not None or m.credit is not None):
            m.sign = "debit_credit"
        elif m.sign == "debit_credit" and m.debit is None and m.credit is None and m.amount is not None:
            m.sign = "as_is"
        m.has_header = bool(m.has_header)
        m.flip_sign = bool(m.flip_sign)
        m.day_first = bool(m.day_first)
        m.currency_columns = {str(k).upper(): int(v) for k, v in (m.currency_columns or {}).items()}
        m.debit_types = [str(t).lower() for t in (m.debit_types or [])]
        return m

    @property
    def first_data_row(self):
        return self.skip_rows + (1 if self.has_header else 0)


@dataclass
class ParsedRow:
    row_index: int
    txn_date: date | None
    posted_date: date | None
    description: str
    amount: Decimal | None
    balance: Decimal | None
    raw: dict
    problems: list[str] = field(default_factory=list)

    @property
    def is_valid(self):
        return self.txn_date is not None and self.amount is not None


@dataclass
class ParseResult:
    rows: list[ParsedRow]
    profile: str | None = None
    profile_confidence: float = 0.0
    mapping: Mapping | None = None
    period: tuple | None = None
    warnings: list[str] = field(default_factory=list)
    header: list[str] | None = None
    sample: list[list[str]] = field(default_factory=list)
    ocr_applied: bool = False
