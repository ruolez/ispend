"""Pairing money moved between accounts of one profile (card payments,
internal transfers) so it never counts as spending. Money moving between two profiles (an owner's
draw from the business) has one leg in each set of books and is never paired."""
import re

import db
from util import record_events, rows_json

class AlreadyPaired(ValueError):
    """One leg already belongs to another pair; unpair it first."""


HINT = re.compile(r"\b(PAYMENT|AUTOPAY|TRANSFER|XFER|ONLINE PMT|E-TRANSFER|ETRANSFER|INTERAC|THANK YOU)\b", re.I)


def score(desc_a, desc_b):
    """Confidence that two opposite-amount rows are the same transfer."""
    return 0.9 if HINT.search(desc_a or "") or HINT.search(desc_b or "") else 0.6


def candidates(profile_id, days=4, limit=200):
    rows = db.query(
        """SELECT a.id AS a_id, a.account_id AS a_account_id, a.txn_date AS a_txn_date, a.amount AS a_amount,
                  a.description_raw AS a_description, a.merchant_name AS a_merchant_name,
                  b.id AS b_id, b.account_id AS b_account_id, b.txn_date AS b_txn_date, b.amount AS b_amount,
                  b.description_raw AS b_description, b.merchant_name AS b_merchant_name
           FROM transactions a
           JOIN transactions b
             ON b.profile_id = a.profile_id AND b.account_id <> a.account_id AND b.amount = -a.amount
            AND b.currency = a.currency AND abs(b.txn_date - a.txn_date) <= %s AND b.transfer_pair_id IS NULL
           WHERE a.profile_id = %s AND a.amount < 0 AND a.transfer_pair_id IS NULL
           ORDER BY a.txn_date DESC, a.id DESC
           LIMIT %s""",
        (days, profile_id, limit),
    ) or []
    out = []
    for r in rows_json(rows):
        a = {k[2:]: v for k, v in r.items() if k.startswith("a_")}
        b = {k[2:]: v for k, v in r.items() if k.startswith("b_")}
        out.append({"a": a, "b": b, "confidence": score(a["description"], b["description"])})
    out.sort(key=lambda c: (-c["confidence"], c["a"]["txn_date"]), reverse=False)
    return out


def _transfer_category_id(profile_id, account_types):
    slug = "transfers.card_payment" if "credit_card" in account_types else "transfers.internal"
    for s in (slug, "transfers"):
        row = db.query("SELECT id FROM categories WHERE profile_id = %s AND slug = %s", (profile_id, s), one=True)
        if row:
            return row["id"]
    return None


def pair(scope, a_id, b_id):
    if a_id == b_id:
        raise ValueError("Pick two different transactions")
    rows = db.query(
        """SELECT t.id, t.account_id, t.amount, t.transfer_pair_id, a.account_type
           FROM transactions t JOIN accounts a ON a.id = t.account_id
           WHERE t.profile_id = %s AND t.id = ANY(%s)""",
        (scope.pid, [a_id, b_id]),
    ) or []
    if len(rows) != 2:
        raise LookupError("Transaction not found")
    by_id = {r["id"]: r for r in rows}
    a, b = by_id[a_id], by_id[b_id]
    if a["account_id"] == b["account_id"]:
        raise ValueError("Both transactions are in the same account")
    if a["amount"] != -b["amount"]:
        raise ValueError("Amounts do not offset each other")
    for leg in (a, b):
        if leg["transfer_pair_id"] and leg["transfer_pair_id"] not in (a["id"], b["id"]):
            raise AlreadyPaired("One of these transactions is already paired with another; unpair it first")
    category_id = _transfer_category_id(scope.pid, {a["account_type"], b["account_type"]})
    with db.transaction():
        for me, other in ((a, b), (b, a)):
            db.execute(
                """UPDATE transactions SET is_transfer = TRUE, is_excluded = TRUE, transfer_pair_id = %s,
                       category_id = COALESCE(%s, category_id),
                       category_status = CASE WHEN %s IS NULL THEN category_status ELSE 'confirmed' END,
                       category_source = CASE WHEN %s IS NULL THEN category_source ELSE 'manual' END,
                       category_rule_id = NULL, category_confidence = 1.0, updated_at = now()
                   WHERE id = %s""",
                (other["id"], category_id, category_id, category_id, me["id"]),
                commit=False,
            )
    record_events([(a["id"], "transfer", {"paired_with": b["id"]}, scope.uid),
                   (b["id"], "transfer", {"paired_with": a["id"]}, scope.uid)])
    return {"a_id": a["id"], "b_id": b["id"], "category_id": category_id}


def unpair(scope, txn_id):
    row = db.query("SELECT id, transfer_pair_id FROM transactions WHERE profile_id = %s AND id = %s",
                   (scope.pid, txn_id), one=True)
    if not row:
        raise LookupError("Transaction not found")
    ids = [row["id"]] + ([row["transfer_pair_id"]] if row["transfer_pair_id"] else [])
    db.execute(
        """UPDATE transactions SET is_transfer = FALSE, is_excluded = FALSE, transfer_pair_id = NULL, updated_at = now()
           WHERE profile_id = %s AND id = ANY(%s)""",
        (scope.pid, ids),
    )
    record_events([(i, "transfer", {"unpaired": True}, scope.uid) for i in ids])
    return len(ids)


def auto_pair(scope, min_confidence=0.9, days=4):
    used = set()
    paired = 0
    for c in candidates(scope.pid, days=days, limit=1000):
        if c["confidence"] < min_confidence:
            continue
        a_id, b_id = c["a"]["id"], c["b"]["id"]
        if a_id in used or b_id in used:
            continue
        try:
            pair(scope, a_id, b_id)
        except (ValueError, LookupError):
            continue
        used.update((a_id, b_id))
        paired += 1
    return paired
