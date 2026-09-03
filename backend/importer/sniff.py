"""File kind sniffing, text decoding, hashing. Pure."""
import hashlib

KINDS = ("csv", "xlsx", "xls", "pdf")


def file_kind(filename, head):
    """'csv'|'xlsx'|'xls'|'pdf' from magic bytes, falling back to extension."""
    head = head or b""
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext in ("csv", "txt", "tsv"):
        return "csv"
    if ext == "xlsx":
        return "xlsx"
    if ext == "xls":
        return "xls"
    if ext == "pdf":
        return "pdf"
    try:
        head.decode("utf-8")
        return "csv"
    except UnicodeDecodeError:
        pass
    try:
        head.decode("cp1252")
        if all(b >= 9 for b in head[:512]):
            return "csv"
    except UnicodeDecodeError:
        pass
    raise ValueError("Unsupported file type; upload a CSV, XLSX, XLS or PDF")


def decode_text(data):
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
