from flask import Blueprint, jsonify, request, session

import categorizer
import db
import rules as rules_mod
from auth import login_required
from util import api_error, audit, json_body, parse_int_list, record_events, rows_json

bp = Blueprint("rules", __name__, url_prefix="/api/rules")

BATCH = 2000
SCAN_FIELDS = "id, description_clean, description_raw, merchant_key, amount, account_id, category_id, is_transfer, is_excluded"


def _scan(user_id, only_uncategorized, scope_ids=None):
    """Yield the user's transactions in batches with the fields the matcher needs."""
    sql = f"SELECT {SCAN_FIELDS} FROM transactions WHERE user_id = %s"
    params = [user_id]
    if only_uncategorized:
        sql += " AND (category_id IS NULL OR category_status = 'suggested') AND NOT is_transfer"
    if scope_ids:
        sql += " AND id = ANY(%s)"
        params.append(list(scope_ids))
    sql += " AND id > %s ORDER BY id LIMIT %s"
    last = 0
    while True:
        rows = db.query(sql, params + [last, BATCH]) or []
        if not rows:
            return
        yield from rows
        last = rows[-1]["id"]
        if len(rows) < BATCH:
            return


def _transfer_category_id(user_id):
    for slug in ("transfers.internal", "transfers"):
        row = db.query("SELECT id FROM categories WHERE user_id = %s AND slug = %s", (user_id, slug), one=True)
        if row:
            return row["id"]
    return None


def apply_rules(user_id, rule_rows, only_uncategorized=True, scope_ids=None):
    """Run the given rules (already in priority order) over the user's
    transactions; first matching rule wins per row. Returns rows updated."""
    compiled = [rules_mod.compile_rule(dict(r)) for r in rule_rows if r.get("is_active", True)]
    if not compiled:
        return 0
    hits = {}
    for txn in _scan(user_id, only_uncategorized, scope_ids):
        rule = rules_mod.first_match(compiled, dict(txn))
        if rule:
            hits.setdefault(rule["id"], []).append(txn["id"])
    categorizer.deactivate_timed_out(compiled)
    updated = 0
    transfer_cat = None
    for rule in compiled:
        ids = hits.get(rule["id"])
        if not ids:
            continue
        category_id = rule.get("category_id")
        if category_id is None and rule.get("set_transfer"):
            transfer_cat = transfer_cat or _transfer_category_id(user_id)
            category_id = transfer_cat
        db.execute(
            """UPDATE transactions SET
                   category_id = COALESCE(%s, category_id),
                   category_status = CASE WHEN %s IS NULL THEN category_status ELSE 'confirmed' END,
                   category_source = 'rule', category_rule_id = %s, category_confidence = 1.0,
                   is_transfer = is_transfer OR %s,
                   is_excluded = is_excluded OR %s OR %s,
                   updated_at = now()
               WHERE id = ANY(%s)""",
            (category_id, category_id, rule["id"], bool(rule.get("set_transfer")),
             bool(rule.get("set_excluded")), bool(rule.get("set_transfer")), ids),
        )
        db.execute("UPDATE rules SET hit_count = hit_count + %s, last_hit_at = now() WHERE id = %s", (len(ids), rule["id"]))
        record_events([(i, "rule", {"rule_id": rule["id"], "category_id": category_id}, user_id) for i in ids])
        updated += len(ids)
    return updated


def _own_rule(rule_id, uid):
    return db.query("SELECT * FROM rules WHERE id = %s AND user_id = %s", (rule_id, uid), one=True)


def _validate_refs(clean, uid):
    if clean.get("category_id") is not None:
        if not db.query("SELECT 1 FROM categories WHERE id = %s AND user_id = %s", (clean["category_id"], uid), one=True):
            raise ValueError("Category not found")
    if clean.get("account_id") is not None:
        if not db.query("SELECT 1 FROM accounts WHERE id = %s AND user_id = %s", (clean["account_id"], uid), one=True):
            raise ValueError("Account not found")


@bp.get("")
@login_required
def list_rules():
    rows = db.query("SELECT * FROM rules WHERE user_id = %s ORDER BY priority, id", (session["user_id"],)) or []
    return jsonify(rows_json(rows))


@bp.post("")
@login_required
def create_rule():
    uid = session["user_id"]
    data = json_body()
    try:
        clean = rules_mod.validate(data)
        _validate_refs(clean, uid)
    except ValueError as e:
        return api_error(str(e))
    if "priority" not in data:
        top = db.query("SELECT COALESCE(MAX(priority), 0) AS p FROM rules WHERE user_id = %s", (uid,), one=True)
        clean["priority"] = (top["p"] if top else 0) + 10
    with db.transaction():
        row = db.execute(
            """INSERT INTO rules (user_id, name, priority, is_active, match_type, match_field, pattern, case_sensitive,
                   amount_min, amount_max, account_id, category_id, set_transfer, set_excluded)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (uid, clean["name"], clean["priority"], clean["is_active"], clean["match_type"], clean["match_field"],
             clean["pattern"], clean["case_sensitive"], clean["amount_min"], clean["amount_max"], clean["account_id"],
             clean["category_id"], clean["set_transfer"], clean["set_excluded"]),
            returning=True,
        )
        applied = 0
        if data.get("apply_existing"):
            applied = apply_rules(uid, [dict(row)], only_uncategorized=bool(data.get("only_uncategorized", True)))
    audit("rule.create", {"id": row["id"], "applied": applied})
    return jsonify({"id": row["id"], "applied": applied}), 201


@bp.put("/reorder")
@login_required
def reorder():
    uid = session["user_id"]
    ids = parse_int_list(json_body().get("ids"))
    with db.transaction():
        for i, rid in enumerate(ids):
            db.execute("UPDATE rules SET priority = %s, updated_at = now() WHERE id = %s AND user_id = %s",
                       (i * 10, rid, uid), commit=False)
    audit("rule.reorder", {"count": len(ids)})
    return jsonify({"ok": True})


@bp.post("/preview")
@login_required
def preview():
    uid = session["user_id"]
    data = json_body()
    try:
        clean = rules_mod.validate(data, require_target=False)
    except ValueError as e:
        return api_error(str(e))
    clean["id"] = data.get("id") or 0
    rule = rules_mod.compile_rule(clean)
    only_uncategorized = bool(data.get("only_uncategorized", False))
    count, sample = 0, []
    for txn in _scan(uid, only_uncategorized):
        if rules_mod.matches(rule, dict(txn)):
            count += 1
            if len(sample) < 20:
                sample.append(txn["id"])
    categorizer.deactivate_timed_out([rule])
    rows = []
    if sample:
        rows = db.query(
            """SELECT id, txn_date, description_raw, merchant_name, amount, category_id, account_id
               FROM transactions WHERE id = ANY(%s) ORDER BY txn_date DESC""", (sample,)) or []
    return jsonify({"count": count, "sample": rows_json(rows)})


@bp.post("/run")
@login_required
def run_all():
    uid = session["user_id"]
    data = json_body()
    rows = db.query("SELECT * FROM rules WHERE user_id = %s AND is_active ORDER BY priority, id", (uid,)) or []
    updated = apply_rules(uid, [dict(r) for r in rows], only_uncategorized=bool(data.get("only_uncategorized", True)))
    audit("rule.run_all", {"updated": updated})
    return jsonify({"updated": updated})


@bp.get("/<int:rule_id>")
@login_required
def get_rule(rule_id):
    row = _own_rule(rule_id, session["user_id"])
    if not row:
        return api_error("Rule not found", 404)
    return jsonify(rows_json([row])[0])


@bp.put("/<int:rule_id>")
@login_required
def update_rule(rule_id):
    uid = session["user_id"]
    existing = _own_rule(rule_id, uid)
    if not existing:
        return api_error("Rule not found", 404)
    data = json_body()
    merged = {**dict(existing), **{k: v for k, v in data.items() if k not in ("apply_existing", "only_uncategorized")}}
    try:
        clean = rules_mod.validate(merged)
        _validate_refs(clean, uid)
    except ValueError as e:
        return api_error(str(e))
    row = db.execute(
        """UPDATE rules SET name=%s, priority=%s, is_active=%s, match_type=%s, match_field=%s, pattern=%s,
               case_sensitive=%s, amount_min=%s, amount_max=%s, account_id=%s, category_id=%s,
               set_transfer=%s, set_excluded=%s, updated_at=now()
           WHERE id=%s RETURNING *""",
        (clean["name"], clean["priority"], clean["is_active"], clean["match_type"], clean["match_field"],
         clean["pattern"], clean["case_sensitive"], clean["amount_min"], clean["amount_max"], clean["account_id"],
         clean["category_id"], clean["set_transfer"], clean["set_excluded"], rule_id),
        returning=True,
    )
    applied = 0
    if data.get("apply_existing"):
        applied = apply_rules(uid, [dict(row)], only_uncategorized=bool(data.get("only_uncategorized", True)))
    audit("rule.update", {"id": rule_id, "applied": applied})
    return jsonify({"ok": True, "applied": applied})


@bp.delete("/<int:rule_id>")
@login_required
def delete_rule(rule_id):
    uid = session["user_id"]
    if not _own_rule(rule_id, uid):
        return api_error("Rule not found", 404)
    db.execute("DELETE FROM rules WHERE id = %s", (rule_id,))
    audit("rule.delete", {"id": rule_id})
    return jsonify({"ok": True})


@bp.post("/<int:rule_id>/apply")
@login_required
def apply_one(rule_id):
    uid = session["user_id"]
    row = _own_rule(rule_id, uid)
    if not row:
        return api_error("Rule not found", 404)
    data = json_body()
    updated = apply_rules(uid, [dict(row)], only_uncategorized=bool(data.get("only_uncategorized", True)))
    audit("rule.apply", {"id": rule_id, "updated": updated})
    return jsonify({"updated": updated})
