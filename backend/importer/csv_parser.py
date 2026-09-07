"""CSV/XLSX table parsing into ParsedRows. Pure except for bank_profiles/generic imports."""
import csv
import io
import re
from decimal import Decimal

from importer import generic
from importer.amounts import parse_amount
from importer.dates import parse_date
from importer.models import Mapping, ParsedRow, ParseResult

DELIMITERS = [",", ";", "\t", "|"]


def detect_delimiter(text):
    lines = [ln for ln in text.splitlines()[:30] if ln.strip()]
    best, best_score = ",", -1
    for d in DELIMITERS:
        counts = [ln.count(d) for ln in lines]
        if not counts or max(counts) == 0:
            continue
        common = max(set(counts), key=counts.count)
        if common == 0:
            continue
        score = sum(1 for c in counts if c == common) * 10 + common
        if score > best_score:
            best, best_score = d, score
    return best


def read_table(text, delimiter=None):
    """List of stripped cell rows; fully-empty rows dropped."""
    delimiter = delimiter or detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, skipinitialspace=True)
    rows = []
    for r in reader:
        cells = [c.replace("﻿", "").strip() for c in r]
        if any(cells):
            rows.append(cells)
    return rows, delimiter


def _cell(row, idx):
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return str(row[idx]).strip()


def parse_table(rows, mapping):
    """Apply a Mapping to raw rows."""
    out = []
    for idx in range(mapping.first_data_row, len(rows)):
        row = rows[idx]
        problems = []
        date_text = _cell(row, mapping.date)
        txn_date = parse_date(date_text, mapping.date_format, mapping.day_first) if date_text else None
        if date_text and txn_date is None:
            problems.append(f"Unparseable date '{date_text}'")
        posted_text = _cell(row, mapping.posted_date)
        posted = parse_date(posted_text, mapping.date_format, mapping.day_first) if posted_text else None
        description = re.sub(r"\s+", " ", " ".join(p for p in (_cell(row, i) for i in mapping.description) if p)).strip()
        amount = None
        if mapping.sign == "debit_credit":
            debit = parse_amount(_cell(row, mapping.debit))
            credit = parse_amount(_cell(row, mapping.credit))
            if debit is not None and debit != 0:
                amount = -abs(debit)
            elif credit is not None:
                amount = abs(credit)
            elif debit is not None:
                amount = Decimal("0.00")
        else:
            amt_text = _cell(row, mapping.amount)
            if not amt_text and mapping.currency_columns:
                for _cur, col in mapping.currency_columns.items():
                    amt_text = _cell(row, col)
                    if amt_text:
                        break
            amount = parse_amount(amt_text)
            if amount is not None:
                if mapping.type_col is not None and mapping.debit_types:
                    t = _cell(row, mapping.type_col).lower()
                    amount = -abs(amount) if t in mapping.debit_types else abs(amount)
                elif mapping.sign == "flip":
                    amount = -amount
            elif amt_text:
                problems.append(f"Unparseable amount '{amt_text}'")
        if amount is not None and mapping.flip_sign:
            amount = -amount
        if amount is None and not problems:
            problems.append("No amount")
        if txn_date is None and not date_text:
            problems.append("No date")
        balance = parse_amount(_cell(row, mapping.balance))
        if txn_date is None and amount is None and not description:
            continue
        out.append(ParsedRow(
            row_index=idx, txn_date=txn_date, posted_date=posted, description=description,
            amount=amount, balance=balance, raw={"cells": list(row)}, problems=problems,
        ))
    return out


def parse_rows(rows, mapping=None, profile_key=None, delimiter=","):
    """Detect profile, build mapping (unless given), parse. Shared by CSV and Excel."""
    import bank_profiles

    warnings = []
    profile, confidence, header_idx, fmt = bank_profiles.detect_csv(rows)
    if profile_key:
        forced = bank_profiles.get(profile_key)
        if forced is not None:
            profile, confidence = forced, 1.0
            f = bank_profiles.match_csv_format(forced, rows)
            header_idx, fmt = (f[1], f[2]) if f else (None, None)
    if mapping is None:
        if profile is not None and fmt is not None:
            mapping = bank_profiles.build_mapping(profile, fmt, rows, header_idx)
        if mapping is None:
            mapping, warnings = generic.guess_mapping(rows)
            if profile is None:
                warnings.insert(0, "Bank not recognised; columns were detected heuristically. Check the mapping.")
    mapping.delimiter = delimiter
    parsed = parse_table(rows, mapping)
    header = rows[mapping.skip_rows] if mapping.has_header and mapping.skip_rows < len(rows) else None
    start = mapping.first_data_row
    sample = rows[start:start + 5]
    valid = [r for r in parsed if r.is_valid]
    if parsed and not valid:
        warnings.append("No valid transactions found with this mapping.")
    period = None
    dates = [r.txn_date for r in valid]
    if dates:
        period = (min(dates), max(dates))
    result = ParseResult(
        rows=parsed, profile=profile.key if profile else None, profile_confidence=confidence,
        mapping=mapping, period=period, warnings=warnings, header=header, sample=sample,
    )
    result.account_type_hint = fmt.account_type_hint() if hasattr(fmt, "account_type_hint") else None
    return result


def parse_csv(text, mapping=None, profile_key=None):
    rows, delimiter = read_table(text, mapping.delimiter if mapping and mapping.delimiter else None)
    if mapping is not None and not isinstance(mapping, Mapping):
        mapping = Mapping.from_dict(mapping)
    return parse_rows(rows, mapping=mapping, profile_key=profile_key, delimiter=delimiter)
