"""Moving accounts from one profile to another, with their transactions, statements, account rules
and column layouts. Categories, tags and merchant memory belong to a profile, so the moved rows are
mapped onto the target's own (same slug, else same name under the same parent, else created there).
Transfer pairs whose other leg stays behind are unlinked: across profiles they are no longer one
transfer. Everything happens in one transaction, and the foreign keys checked at COMMIT prove that
no reference crosses profiles afterwards.
"""
import json
from urllib.parse import parse_qsl, urlencode

import db
from profiles import BUSY_STATUSES, ProfileError
from util import audit, record_events

# The same-profile foreign keys a move updates on both sides; they are checked at COMMIT.
DEFERRED = ("fk_txn_account", "fk_txn_category", "fk_txn_statement", "fk_stmt_account", "fk_rule_account",
            "fk_rule_category", "fk_layout_account", "fk_mm_category", "fk_budget_category", "fk_category_parent")


def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _plan(cur, uid, src, dst, account_ids, renames):
    """What a move would do, without writing anything."""
    cur.execute("SELECT id, name FROM accounts WHERE id = ANY(%s) AND profile_id = %s ORDER BY name", (account_ids, src))
    accounts = _rows(cur)
    if not accounts or len(accounts) != len(set(account_ids)):
        raise ProfileError("Account not found", 404)
    cur.execute("SELECT name FROM accounts WHERE profile_id = %s", (dst,))
    taken = {r[0] for r in cur.fetchall()}
    collisions = [a for a in accounts if renames.get(a["id"], a["name"]) in taken]
    cur.execute("SELECT id, original_filename, status FROM statements WHERE account_id = ANY(%s) AND status = ANY(%s)",
                (account_ids, list(BUSY_STATUSES)))
    busy = _rows(cur)

    # Categories the moved rows use, with their parents, parents first.
    cur.execute("""
        WITH RECURSIVE used AS (
            SELECT category_id AS id FROM transactions WHERE account_id = ANY(%(x)s)
            UNION SELECT s.category_id FROM transaction_splits s JOIN transactions t ON t.id = s.transaction_id
                   WHERE t.account_id = ANY(%(x)s)
            UNION SELECT r.category_id FROM import_rows r JOIN statements st ON st.id = r.statement_id
                   WHERE st.account_id = ANY(%(x)s)
            UNION SELECT category_id FROM rules WHERE account_id = ANY(%(x)s)
            UNION SELECT m.category_id FROM merchant_memory m
                   WHERE m.profile_id = %(a)s AND m.merchant_key IN (SELECT merchant_key FROM transactions WHERE account_id = ANY(%(x)s))
        ), tree AS (
            SELECT c.id, c.parent_id FROM categories c JOIN used u ON u.id = c.id WHERE c.profile_id = %(a)s
            UNION SELECT p.id, p.parent_id FROM categories p JOIN tree t ON t.parent_id = p.id
        )
        SELECT c.id, c.parent_id, c.name, c.slug, c.kind, c.color, c.icon, c.is_system, c.sort_order
          FROM categories c WHERE c.id IN (SELECT id FROM tree)
         ORDER BY c.parent_id IS NOT NULL, c.sort_order, c.id""", {"x": account_ids, "a": src})
    needed = _rows(cur)
    cur.execute("SELECT id, parent_id, name, slug, kind FROM categories WHERE profile_id = %s", (dst,))
    target = _rows(cur)
    by_slug = {c["slug"]: c for c in target}
    by_name = {(c["parent_id"], c["name"].lower()): c for c in target}
    cat_map, created = {}, []
    for c in needed:
        parent = cat_map.get(c["parent_id"]) if c["parent_id"] else None
        hit = by_slug.get(c["slug"])
        if not (hit and hit["kind"] == c["kind"]):
            hit = by_name.get((parent, c["name"].lower()))
            hit = hit if hit and hit["kind"] == c["kind"] else None
        if hit:
            cat_map[c["id"]] = hit["id"]
            continue
        cat_map[c["id"]] = ("new", c["id"])          # created by the move; a placeholder until then
        created.append({**c, "parent": parent})

    cur.execute("""SELECT DISTINCT g.id, g.name FROM tags g JOIN transaction_tags tt ON tt.tag_id = g.id
                    JOIN transactions t ON t.id = tt.transaction_id WHERE t.account_id = ANY(%s)""", (account_ids,))
    tags = _rows(cur)
    cur.execute("SELECT id, name FROM tags WHERE profile_id = %s", (dst,))
    target_tags = {r["name"].lower(): r["id"] for r in _rows(cur)}

    cur.execute("""SELECT t.id, t.transfer_pair_id FROM transactions t JOIN transactions o ON o.id = t.transfer_pair_id
                    WHERE t.account_id = ANY(%s) AND NOT (o.account_id = ANY(%s))""", (account_ids, account_ids))
    crossing = _rows(cur)
    cur.execute("""SELECT (SELECT COUNT(*) FROM transactions WHERE account_id = ANY(%(x)s)) AS transactions,
                          (SELECT COUNT(*) FROM statements WHERE account_id = ANY(%(x)s)) AS statements,
                          (SELECT COUNT(*) FROM rules WHERE account_id = ANY(%(x)s)) AS rules,
                          (SELECT COUNT(*) FROM import_layouts WHERE account_id = ANY(%(x)s)) AS layouts""", {"x": account_ids})
    counts = _rows(cur)[0]
    return {
        "accounts": accounts, "collisions": collisions, "busy": busy, "counts": counts,
        "needed": needed, "cat_map": cat_map, "created": created,
        "tags": tags, "target_tags": target_tags, "crossing": crossing,
    }


def preview(uid, src, dst, account_ids, renames=None):
    with db.get_db().cursor() as cur:
        plan = _plan(cur, uid, src, dst, account_ids, renames or {})
    db.get_db().rollback()
    names = {c["id"]: c["name"] for c in plan["needed"]}
    return {
        "accounts": plan["accounts"],
        "counts": {**{k: int(v) for k, v in plan["counts"].items()},
                   "transfers_unlinked": len(plan["crossing"])},
        "categories_matched": sorted({names[s] for s, d in plan["cat_map"].items() if not isinstance(d, tuple)}),
        "categories_created": [c["name"] for c in plan["created"]],
        "tags_created": sorted(t["name"] for t in plan["tags"] if t["name"].lower() not in plan["target_tags"]),
        "collisions": [{"id": a["id"], "name": a["name"]} for a in plan["collisions"]],
        "busy": [{"id": s["id"], "filename": s["original_filename"]} for s in plan["busy"]],
    }


def _unique(cur, sql, params, value, make):
    """value, or the first make(n) for n = 2, 3, … that sql (one %s for the candidate) does not find."""
    candidate, n = value, 2
    while True:
        cur.execute(sql, (*params, candidate))
        if not cur.fetchone():
            return candidate
        candidate, n = make(n), n + 1


def move(uid, src, dst, account_ids, renames=None):
    """Move the accounts and everything that belongs to them from profile src to profile dst."""
    renames = {int(k): " ".join(str(v).split())[:200] for k, v in (renames or {}).items() if str(v).strip()}
    conn = db.get_db()
    with db.transaction(), conn.cursor() as cur:
        cur.execute(f"SET CONSTRAINTS {', '.join(DEFERRED)} DEFERRED")
        # Moved rows keep is_transfer / is_excluded exactly as they are (see migration 030).
        cur.execute("SET LOCAL ispend.keep_transfer_flags = 'on'")
        cur.execute("SELECT id FROM accounts WHERE id = ANY(%s) AND profile_id = %s FOR UPDATE", (account_ids, src))
        cur.execute("SELECT id FROM statements WHERE account_id = ANY(%s) FOR UPDATE", (account_ids,))
        plan = _plan(cur, uid, src, dst, account_ids, renames)
        if plan["busy"]:
            raise ProfileError("An import into one of these accounts is still running. Try again when it has finished.", 409)
        if plan["collisions"]:
            names = ", ".join(a["name"] for a in plan["collisions"])
            raise ProfileError(f"The other profile already has an account named {names}. Choose a new name to move it.", 409)

        # Categories the target lacks, parents first, with a slug and name free in the target.
        cat_map = dict(plan["cat_map"])
        for c in plan["created"]:
            parent = cat_map.get(c["parent_id"]) if c["parent_id"] else None
            slug = _unique(cur, "SELECT 1 FROM categories WHERE profile_id = %s AND slug = %s", (dst,), c["slug"],
                           lambda n, s=c["slug"]: f"{s}_{n}")
            name = _unique(cur, """SELECT 1 FROM categories WHERE profile_id = %s AND COALESCE(parent_id, 0) = %s
                                    AND lower(name) = lower(%s)""", (dst, parent or 0), c["name"],
                           lambda n, s=c["name"]: f"{s} {n}")
            cur.execute("""INSERT INTO categories (user_id, profile_id, parent_id, name, slug, kind, color, icon, is_system, sort_order)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (uid, dst, parent, name, slug, c["kind"], c["color"], c["icon"], c["is_system"], c["sort_order"]))
            cat_map[c["id"]] = cur.fetchone()[0]
        tag_map = {}
        for t in plan["tags"]:
            have = plan["target_tags"].get(t["name"].lower())
            if have is None:
                cur.execute("INSERT INTO tags (user_id, profile_id, name) VALUES (%s, %s, %s) RETURNING id", (uid, dst, t["name"]))
                have = cur.fetchone()[0]
            tag_map[t["id"]] = have
        cur.execute("CREATE TEMP TABLE mv_cat (src INT PRIMARY KEY, dst INT NOT NULL) ON COMMIT DROP")
        if cat_map:
            cur.execute("INSERT INTO mv_cat VALUES " + ", ".join(["(%s, %s)"] * len(cat_map)),
                        [v for pair in cat_map.items() for v in pair])
        cur.execute("CREATE TEMP TABLE mv_tag (src INT PRIMARY KEY, dst INT NOT NULL) ON COMMIT DROP")
        if tag_map:
            cur.execute("INSERT INTO mv_tag VALUES " + ", ".join(["(%s, %s)"] * len(tag_map)),
                        [v for pair in tag_map.items() for v in pair])
        x = list(account_ids)

        # A transfer whose other leg stays behind is two separate rows now, one in each profile.
        legs = sorted({r["id"] for r in plan["crossing"]} | {r["transfer_pair_id"] for r in plan["crossing"]})
        if legs:
            cur.execute("""
                UPDATE transactions p SET transfer_pair_id = NULL, updated_at = now(),
                       is_transfer = p.is_transfer AND EXISTS (SELECT 1 FROM categories c WHERE c.id = p.category_id AND c.kind = 'transfer'),
                       is_excluded = p.is_excluded AND EXISTS (SELECT 1 FROM categories c WHERE c.id = p.category_id AND c.kind = 'transfer')
                 WHERE p.id = ANY(%s)""", (legs,))

        cur.execute("SELECT id FROM rules WHERE account_id = ANY(%s)", (x,))
        moved_rules = [r[0] for r in cur.fetchall()]
        cur.execute("""UPDATE transactions t SET profile_id = %s,
                              category_id = (SELECT dst FROM mv_cat WHERE src = t.category_id),
                              category_rule_id = CASE WHEN t.category_rule_id = ANY(%s) THEN t.category_rule_id END
                        WHERE t.account_id = ANY(%s)""", (dst, moved_rules, x))
        cur.execute("""UPDATE transaction_splits s SET category_id = (SELECT dst FROM mv_cat WHERE src = s.category_id)
                         FROM transactions t WHERE t.id = s.transaction_id AND t.account_id = ANY(%s)
                          AND s.category_id IS NOT NULL""", (x,))
        cur.execute("""UPDATE transaction_tags tt SET tag_id = m.dst FROM mv_tag m, transactions t
                        WHERE t.id = tt.transaction_id AND t.account_id = ANY(%s) AND tt.tag_id = m.src""", (x,))

        cur.execute("UPDATE statements SET profile_id = %s, updated_at = now() WHERE account_id = ANY(%s)", (dst, x))
        cur.execute("""UPDATE import_rows r SET category_id = (SELECT dst FROM mv_cat WHERE src = r.category_id),
                              category_rule_id = CASE WHEN r.category_rule_id = ANY(%s) THEN r.category_rule_id END
                         FROM statements st WHERE st.id = r.statement_id AND st.account_id = ANY(%s)""", (moved_rules, x))
        cur.execute("UPDATE rules r SET profile_id = %s, category_id = (SELECT dst FROM mv_cat WHERE src = r.category_id), "
                    "updated_at = now() WHERE r.account_id = ANY(%s)", (dst, x))
        cur.execute("UPDATE import_layouts SET profile_id = %s WHERE account_id = ANY(%s)", (dst, x))

        # A statement and its rows can sit in different accounts (a committed statement's account
        # can be changed); where the move splits them across profiles, the link goes.
        cur.execute("""UPDATE transactions t SET statement_id = NULL FROM statements st
                        WHERE st.id = t.statement_id AND st.profile_id <> t.profile_id AND t.user_id = %s""", (uid,))
        cur.execute("""UPDATE import_rows r SET duplicate_of = NULL FROM statements st, transactions d
                        WHERE st.id = r.statement_id AND d.id = r.duplicate_of AND d.profile_id <> st.profile_id
                          AND st.user_id = %s""", (uid,))

        # What the source profile learned about these merchants comes along, so the next statement
        # of the moved accounts is recognised in the target too.
        cur.execute("""INSERT INTO merchant_memory (user_id, profile_id, merchant_key, direction, display_name, category_id,
                                                    is_transfer, times_used, last_used_at)
                       SELECT m.user_id, %(b)s, m.merchant_key, m.direction, m.display_name, mc.dst, m.is_transfer,
                              m.times_used, m.last_used_at
                         FROM merchant_memory m JOIN mv_cat mc ON mc.src = m.category_id
                        WHERE m.profile_id = %(a)s
                          AND m.merchant_key IN (SELECT merchant_key FROM transactions WHERE account_id = ANY(%(x)s))
                       ON CONFLICT (profile_id, merchant_key, direction) DO NOTHING""", {"a": src, "b": dst, "x": x})
        cur.execute("""INSERT INTO recurring_dismissals (user_id, profile_id, merchant_key)
                       SELECT user_id, %(b)s, merchant_key FROM recurring_dismissals
                        WHERE profile_id = %(a)s
                          AND merchant_key IN (SELECT merchant_key FROM transactions WHERE account_id = ANY(%(x)s))
                       ON CONFLICT DO NOTHING""", {"a": src, "b": dst, "x": x})

        for a in plan["accounts"]:
            cur.execute("UPDATE accounts SET profile_id = %s, name = %s, updated_at = now() WHERE id = %s",
                        (dst, renames.get(a["id"], a["name"]), a["id"]))
        _forget_accounts(cur, src, set(x))
        cur.execute("DELETE FROM insights WHERE profile_id IN (%s, %s)", (src, dst))
        record_events([(leg, "transfer", {"unpaired": "moved_profile"}, uid) for leg in legs], commit=False)
    result = {"accounts": len(plan["accounts"]), **{k: int(v) for k, v in plan["counts"].items()},
              "categories_created": len(plan["created"]), "transfers_unlinked": len(plan["crossing"])}
    audit("account.move", {"accounts": x, "from_profile": src, "to_profile": dst, **result})
    return result


def _forget_accounts(cur, profile_id, account_ids):
    """The source profile's default account and saved views stop pointing at accounts it no longer has."""
    cur.execute("SELECT preferences FROM profiles WHERE id = %s", (profile_id,))
    prefs = dict(cur.fetchone()[0] or {})
    changed = False
    if prefs.get("default_account_id") in account_ids:
        prefs.pop("default_account_id")
        changed = True
    views = []
    for v in prefs.get("saved_views") or []:
        pairs = parse_qsl(str(v.get("query") or "?")[1:], keep_blank_values=True)
        kept = []
        for key, value in pairs:
            if key == "acct":
                ids = [i for i in value.split(",") if i and not (i.isdigit() and int(i) in account_ids)]
                changed |= len(ids) != len([i for i in value.split(",") if i])
                if not ids:
                    continue
                value = ",".join(ids)
            kept.append((key, value))
        views.append({**v, "query": "?" + urlencode(kept, safe=",:")})
    if changed:
        if "saved_views" in prefs:
            prefs["saved_views"] = views
        cur.execute("UPDATE profiles SET preferences = %s, updated_at = now() WHERE id = %s", (json.dumps(prefs), profile_id))
