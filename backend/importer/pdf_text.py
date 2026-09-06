"""pdfplumber access + the PDF parse entry point."""
from importer import pdf_lines
from importer.dates import find_period
from importer.models import Mapping, ParseResult


def extract_pages(path):
    import pdfplumber

    pages = []
    with pdfplumber.open(path) as pdf:
        for n, page in enumerate(pdf.pages, start=1):
            try:
                page = page.dedupe_chars(tolerance=1)  # bold text is often printed twice, giving "EEUUGGEENNEE"
            except Exception:
                pass
            words = page.extract_words(x_tolerance=1.5, y_tolerance=3, keep_blank_chars=False, extra_attrs=["size"])
            pages.append({
                "page": n,
                "words": [{"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"], "bottom": w["bottom"]}
                          for w in words],
                "width": page.width, "height": page.height,
            })
    return pages


def char_count(path):
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return sum(len(p.chars) for p in pdf.pages), len(pdf.pages)


def all_lines(pages):
    lines = []
    for p in pages:
        lines.extend(pdf_lines.cluster_rows(p["words"], page=p["page"]))
    return lines


def page_text(pages, n=2):
    return pdf_lines.lines_text(all_lines(pages[:n]))


def parse_pdf(path, profile_key=None, flip_sign=False, pages=None):
    import bank_profiles

    pages = pages if pages is not None else extract_pages(path)
    head = page_text(pages, 2)
    profile, confidence = bank_profiles.detect_pdf(head)
    if profile_key:
        forced = bank_profiles.get(profile_key)
        if forced:
            profile, confidence = forced, 1.0
    period = find_period(head) or find_period(page_text(pages, len(pages)))
    lines = all_lines(pages)
    rows = pdf_lines.rows_to_transactions(lines, profile, period, flip_sign=flip_sign)
    warnings = []
    if profile is None:
        warnings.append("Bank not recognised; transactions were read with a generic layout parser.")
    if not rows:
        warnings.append("No transactions were recognised in this PDF. If it is a scan, OCR quality may be too low.")
    valid = [r for r in rows if r.is_valid]
    if valid and profile is None:
        positives = sum(1 for r in valid if r.amount > 0)
        if positives / len(valid) > 0.85:
            warnings.append("Most amounts are positive; if this is a credit card statement, flip the sign in the preview.")
    if period is None and valid:
        dates = [r.txn_date for r in valid]
        period = (min(dates), max(dates))
    mapping = Mapping(has_header=False, sign=(profile.pdf_sign if profile else "as_is"), flip_sign=flip_sign)
    sample = [[ln.text] for ln in lines[:8]]
    return ParseResult(rows=rows, profile=profile.key if profile else None, profile_confidence=confidence,
                       mapping=mapping, period=period, warnings=warnings, header=None, sample=sample)
