"""Registry of bank profiles and CSV/PDF detection."""
import importlib

from bank_profiles.base import (BankProfile, CsvFormat, build_mapping_from_format, find_format_row)

_MODULES = ("chase", "amex", "capital_one", "bofa", "citi", "discover", "wells_fargo", "rbc", "td", "bmo", "scotiabank", "pnc")
REGISTRY = {}
for _name in _MODULES:
    _mod = importlib.import_module(f"bank_profiles.{_name}")
    REGISTRY[_mod.PROFILE.key] = _mod.PROFILE


def get(key):
    return REGISTRY.get(key)


def all_profiles():
    return sorted(REGISTRY.values(), key=lambda p: p.label)


def match_csv_format(profile, rows):
    """Best (score, header_index, fmt) for this profile, or None."""
    best = None
    for fmt in profile.csv_formats:
        hit = find_format_row(fmt, rows)
        if hit and (best is None or hit[1] > best[0]):
            best = (hit[1], hit[0], fmt)
    if profile.csv_headerless:
        score, mapping = profile.csv_headerless(rows)
        if mapping is not None and score > 0 and (best is None or score > best[0]):
            best = (score, None, mapping)
    return best


def detect_csv(rows):
    """(profile, confidence, header_index, fmt_or_mapping). Score >= 0.6 wins; ties -> first registered."""
    best = (None, 0.0, None, None)
    for profile in REGISTRY.values():
        hit = match_csv_format(profile, rows)
        if hit and hit[0] > best[1]:
            best = (profile, hit[0], hit[1], hit[2])
    if best[1] < 0.6:
        return (None, 0.0, None, None)
    return best


def build_mapping(profile, fmt, rows, header_index):
    from importer.models import Mapping

    if isinstance(fmt, Mapping):
        return fmt
    return build_mapping_from_format(fmt, rows[header_index], header_index)


def detect_pdf(text):
    """(profile, confidence) from marker hits in the first pages' text."""
    low = (text or "").lower()
    best, best_score = None, 0.0
    for profile in REGISTRY.values():
        if not profile.pdf_markers:
            continue
        hits = sum(1 for m in profile.pdf_markers if m.lower() in low)
        # The first marker is the distinctive brand name; it alone is strong evidence.
        score = min(1.0, hits / len(profile.pdf_markers) + (0.4 if profile.pdf_markers[0].lower() in low else 0.0))
        if score > best_score or (score == best_score and best and len(profile.pdf_markers) > len(best.pdf_markers)):
            best, best_score = profile, score
    if best_score < 0.5:
        return None, 0.0
    return best, round(best_score, 3)
