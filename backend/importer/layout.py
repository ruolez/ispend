"""Fingerprint of a tabular statement's column layout, so a mapping the user made once can be
replayed on the next export with the same columns. Pure."""
from importer import generic
from importer.sniff import sha256_bytes


def _column_class(stat):
    if stat["filled"] < 0.2:
        return "e"
    if stat["date"] >= 0.8:
        return "d"
    if stat["money"] >= 0.8:
        return "m"
    return "t"


def fingerprint(rows):
    """(layout_key, header): the key is stable across exports of the same layout and independent
    of any mapping; header is the raw header row for display, None when the file has no header."""
    hdr = generic.find_header_row(rows)
    if hdr is not None:
        cells = [generic.norm_header(c) for c in rows[hdr]]
        while cells and not cells[-1]:
            cells.pop()
        material = "h:" + "\x1f".join(cells)
        header = [str(c) for c in rows[hdr][:len(cells)]]
    else:
        stats = generic.column_profile(rows)
        material = f"c:{len(stats)}:" + "".join(_column_class(s) for s in stats)
        header = None
    return sha256_bytes(material.encode("utf-8")), header
