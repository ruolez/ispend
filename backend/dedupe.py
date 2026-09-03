"""Transaction fingerprints and duplicate detection."""
import hashlib
from collections import Counter
from decimal import Decimal


def fingerprint(account_id, txn_date, amount, description_clean):
    amount = Decimal(str(amount)) if not isinstance(amount, Decimal) else amount
    payload = f"{int(account_id)}|{txn_date.isoformat()}|{amount:.2f}|{description_clean or ''}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def assign_occurrences(rows):
    """rows: objects/dicts with .fingerprint; sets .occurrence = running count per fingerprint (file order)."""
    seen = Counter()
    for r in rows:
        fp = _get(r, "fingerprint")
        if not fp:
            continue
        seen[fp] += 1
        _set(r, "occurrence", seen[fp])


def in_file_duplicates(rows):
    """Indexes of rows whose (fingerprint, occurrence) repeats within the file."""
    seen, dupes = set(), set()
    for i, r in enumerate(rows):
        key = (_get(r, "fingerprint"), _get(r, "occurrence"))
        if not key[0]:
            continue
        if key in seen:
            dupes.add(i)
        seen.add(key)
    return dupes


def find_existing(account_id, pairs):
    """{(fingerprint, occurrence): transaction_id} for pairs already stored on this account."""
    import db

    fps = sorted({fp for fp, _occ in pairs if fp})
    if not fps or not account_id:
        return {}
    rows = db.query(
        "SELECT id, fingerprint, occurrence FROM transactions WHERE account_id = %s AND fingerprint = ANY(%s)",
        (account_id, fps),
    )
    wanted = set(pairs)
    return {(r["fingerprint"], r["occurrence"]): r["id"] for r in rows if (r["fingerprint"], r["occurrence"]) in wanted}


def _get(obj, name):
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _set(obj, name, value):
    if isinstance(obj, dict):
        obj[name] = value
    else:
        setattr(obj, name, value)
