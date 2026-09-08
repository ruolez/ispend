"""Flip the sign of already-imported transactions (money in <-> money out).

Fingerprints include the amount, so every flipped row gets a new fingerprint and a fresh
occurrence index that cannot collide with rows that stay untouched.
"""
import dedupe
import db
from util import record_events

SELECT = """SELECT id, account_id, txn_date, amount, description_clean, fingerprint, occurrence
            FROM transactions WHERE user_id = %s AND id = ANY(%s) ORDER BY txn_date, id"""


def plan_flips(rows, taken):
    """Pure: rows -> [(id, new_amount, new_fingerprint, new_occurrence)].

    `taken` is a set of (fingerprint, occurrence) pairs already used by rows that are NOT
    being flipped; occurrences are assigned so no flipped row lands on a taken pair or on
    another flipped row.
    """
    used = set(taken)
    plan = []
    for r in rows:
        new_amount = -r["amount"]
        fp = dedupe.fingerprint(r["account_id"], r["txn_date"], new_amount, r["description_clean"])
        occ = 1
        while (fp, occ) in used:
            occ += 1
        used.add((fp, occ))
        plan.append((r["id"], new_amount, fp, occ))
    return plan


def flip_signs(user_id, txn_ids, reason=None):
    """Negate amounts for the given transactions of this user. Returns the number flipped."""
    if not txn_ids:
        return 0
    rows = db.query(SELECT, (user_id, list(txn_ids))) or []
    if not rows:
        return 0
    account_ids = sorted({r["account_id"] for r in rows})
    others = db.query(
        "SELECT fingerprint, occurrence FROM transactions WHERE account_id = ANY(%s) AND NOT (id = ANY(%s))",
        (account_ids, [r["id"] for r in rows]),
    ) or []
    plan = plan_flips(rows, {(o["fingerprint"], o["occurrence"]) for o in others})
    ids = [p[0] for p in plan]
    with db.transaction():
        # Park every row on a temporary fingerprint first so the final values never collide
        # with a sibling that has not been rewritten yet.
        db.execute("UPDATE transactions SET fingerprint = fingerprint || ':flip' WHERE id = ANY(%s)", (ids,), commit=False)
        for txn_id, amount, fp, occ in plan:
            db.execute(
                "UPDATE transactions SET amount = %s, fingerprint = %s, occurrence = %s, updated_at = now() WHERE id = %s",
                (amount, fp, occ, txn_id), commit=False,
            )
        db.execute("UPDATE transaction_splits SET amount = -amount WHERE transaction_id = ANY(%s)", (ids,), commit=False)
        record_events([(p[0], "manual", {"flip_sign": True, "reason": reason}, user_id) for p in plan], commit=False)
    return len(plan)
