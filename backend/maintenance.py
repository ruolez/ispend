"""Data maintenance that follows normalizer upgrades: recompute merchant keys/names for a
user's transactions and carry rules and merchant memory along so nothing stops matching."""
import logging
from collections import Counter, defaultdict

import db
import dedupe
import merchant

log = logging.getLogger(__name__)


def _phrases_by_statement(user_id, rows):
    """The cardholder phrases each statement was imported with (saved at parse time, else recomputed from
    that statement's own rows), so renormalized fingerprints keep matching future re-imports. Manual
    rows (no statement) are normalized without phrases, exactly like POST /api/transactions."""
    saved = {}
    for st in db.query("SELECT id, stats FROM statements WHERE user_id = %s", (user_id,)) or []:
        phrases = ((st.get("stats") or {}).get("stripped_phrases"))
        if isinstance(phrases, list):
            saved[st["id"]] = [p for p in phrases if isinstance(p, str)]
    by_statement = defaultdict(list)
    for r in rows:
        if r.get("statement_id") is not None:
            by_statement[r["statement_id"]].append(r["description_raw"])
    return {sid: saved.get(sid, merchant.frequent_phrases(descs)) for sid, descs in by_statement.items()}


def renormalize_user(user_id):
    rows = db.query(
        """SELECT id, account_id, statement_id, txn_date, amount, description_raw, description_clean, merchant_key,
                  merchant_name, fingerprint, occurrence
           FROM transactions WHERE user_id = %s ORDER BY account_id, txn_date, id""",
        (user_id,),
    ) or []
    phrases = _phrases_by_statement(user_id, rows)
    key_votes = defaultdict(Counter)
    changed = []
    for r in rows:
        m = merchant.normalize(r["description_raw"], phrases.get(r.get("statement_id"), ()))
        key_votes[r["merchant_key"]][m.key] += 1
        if (m.key, m.name, m.clean) != (r["merchant_key"], r["merchant_name"], r["description_clean"]):
            changed.append((r, m))
    key_map = {old: votes.most_common(1)[0][0] for old, votes in key_votes.items()}
    key_map = {o: n for o, n in key_map.items() if o != n}
    name_for = {}
    for _r, m in changed:
        name_for.setdefault(m.key, m.name)

    touched_ids = [r["id"] for r, _m in changed]
    used = set()
    if changed:
        others = db.query(
            "SELECT fingerprint, occurrence FROM transactions WHERE user_id = %s AND NOT (id = ANY(%s))",
            (user_id, touched_ids),
        ) or []
        used = {(o["fingerprint"], o["occurrence"]) for o in others}
    plan = []
    for r, m in changed:
        fp = dedupe.fingerprint(r["account_id"], r["txn_date"], r["amount"], m.clean)
        occ = 1
        while (fp, occ) in used:
            occ += 1
        used.add((fp, occ))
        plan.append((r["id"], m.key, m.name, m.clean, fp, occ))

    rules_updated = rules_removed = memory_updated = memory_merged = 0
    with db.transaction():
        if plan:
            db.execute("UPDATE transactions SET fingerprint = fingerprint || ':renorm' WHERE id = ANY(%s)",
                       (touched_ids,), commit=False)
            for txn_id, key, name, clean, fp, occ in plan:
                db.execute(
                    """UPDATE transactions SET merchant_key = %s, merchant_name = %s, description_clean = %s,
                              fingerprint = %s, occurrence = %s, updated_at = now() WHERE id = %s""",
                    (key, name, clean, fp, occ, txn_id), commit=False,
                )
        # Rules keyed on the old merchant key follow it; a rule that would duplicate an
        # existing rule for the new key is dropped (the earlier one keeps its priority).
        rules = db.query(
            "SELECT id, pattern, category_id FROM rules WHERE user_id = %s AND match_field = 'merchant_key' "
            "AND match_type = 'equals' ORDER BY priority, id", (user_id,), commit=False) or []
        seen = {r["pattern"] for r in rules if r["pattern"] not in key_map}
        for r in rules:
            new = key_map.get(r["pattern"])
            if not new:
                continue
            if new in seen:
                db.execute("UPDATE transactions SET category_rule_id = NULL WHERE category_rule_id = %s", (r["id"],), commit=False)
                db.execute("DELETE FROM rules WHERE id = %s", (r["id"],), commit=False)
                rules_removed += 1
                continue
            db.execute("UPDATE rules SET pattern = %s, name = %s, updated_at = now() WHERE id = %s",
                       (new, new, r["id"]), commit=False)
            seen.add(new)
            rules_updated += 1
        memory = db.query(
            "SELECT id, merchant_key, category_id, times_used FROM merchant_memory WHERE user_id = %s ORDER BY times_used DESC, id",
            (user_id,), commit=False) or []
        have = {m["merchant_key"]: m for m in memory if m["merchant_key"] not in key_map}
        for m in memory:
            new = key_map.get(m["merchant_key"])
            if not new:
                continue
            if new in have:
                db.execute("UPDATE merchant_memory SET times_used = times_used + %s WHERE id = %s",
                           (m["times_used"], have[new]["id"]), commit=False)
                db.execute("DELETE FROM merchant_memory WHERE id = %s", (m["id"],), commit=False)
                memory_merged += 1
                continue
            db.execute("UPDATE merchant_memory SET merchant_key = %s, display_name = COALESCE(%s, display_name) WHERE id = %s",
                       (new, name_for.get(new), m["id"]), commit=False)
            have[new] = m
            memory_updated += 1
    return {
        "stripped_phrases": sorted({ph for ps in phrases.values() for ph in ps}),
        "transactions_updated": len(plan),
        "merchant_keys_changed": len(key_map),
        "rules_updated": rules_updated,
        "rules_removed_as_duplicates": rules_removed,
        "memory_updated": memory_updated,
        "memory_merged": memory_merged,
    }


def learn_from_history(user_id):
    """Mine confirmed categorizations into merchant memory: for every merchant the user has
    categorized, the category they used most often (latest wins ties) is remembered."""
    rows = db.query(
        """SELECT merchant_key, category_id, COUNT(*) AS n, MAX(updated_at) AS last_at,
                  BOOL_OR(is_transfer) AS is_transfer
           FROM transactions
           WHERE user_id = %s AND category_id IS NOT NULL AND category_status = 'confirmed' AND merchant_key <> ''
           GROUP BY merchant_key, category_id""",
        (user_id,),
    ) or []
    best = {}
    for r in rows:
        cur = best.get(r["merchant_key"])
        if cur is None or (r["n"], r["last_at"]) > (cur["n"], cur["last_at"]):
            best[r["merchant_key"]] = r
    added = updated = 0
    with db.transaction():
        for key, r in best.items():
            existing = db.query("SELECT id, category_id, times_used FROM merchant_memory WHERE user_id = %s AND merchant_key = %s",
                                (user_id, key), one=True, commit=False)
            if existing is None:
                db.execute(
                    """INSERT INTO merchant_memory (user_id, merchant_key, category_id, is_transfer, times_used)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (user_id, key, r["category_id"], bool(r["is_transfer"]), int(r["n"])), commit=False)
                added += 1
            elif existing["times_used"] < r["n"]:
                db.execute("UPDATE merchant_memory SET times_used = %s WHERE id = %s", (int(r["n"]), existing["id"]), commit=False)
                updated += 1
    return {"merchants_seen": len(best), "memory_added": added, "memory_updated": updated}
