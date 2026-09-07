from flask import Blueprint, jsonify, request, session

import categorizer
import db
from auth import login_required
from util import api_error, audit, json_body, record_events, rows_json, to_int

bp = Blueprint("merchants", __name__, url_prefix="/api/merchants")


@bp.get("")
@login_required
def list_merchants():
    uid = session["user_id"]
    q = (request.args.get("q") or "").strip()
    sql = """
        SELECT m.merchant_key, m.display_name, m.category_id, m.is_transfer, m.times_used, m.last_used_at,
               COALESCE(t.n, 0) AS txn_count, COALESCE(t.total, 0) AS total
        FROM merchant_memory m
        LEFT JOIN (SELECT merchant_key, COUNT(*) AS n, SUM(amount) AS total
                   FROM transactions WHERE user_id = %s GROUP BY merchant_key) t
          ON t.merchant_key = m.merchant_key
        WHERE m.user_id = %s"""
    params = [uid, uid]
    if q:
        sql += " AND (m.merchant_key ILIKE %s OR m.display_name ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY t.n DESC NULLS LAST, m.merchant_key LIMIT 500"
    return jsonify(rows_json(db.query(sql, params) or []))


@bp.put("/<path:merchant_key>")
@login_required
def update_merchant(merchant_key):
    uid = session["user_id"]
    data = json_body()
    category_id = to_int(data.get("category_id"), "Category")
    display_name = str(data.get("display_name") or "").strip() or None
    if category_id is None and not display_name:
        return api_error("category_id or display_name is required")
    if category_id is not None:
        if not db.query("SELECT 1 FROM categories WHERE id = %s AND user_id = %s", (category_id, uid), one=True):
            return api_error("Category not found", 404)
        categorizer.learn(uid, merchant_key, category_id, display_name=display_name, is_transfer=bool(data.get("is_transfer")))
    else:
        # transfer-only merchants have no category to learn; a rename still applies
        db.execute("UPDATE merchant_memory SET display_name = %s WHERE user_id = %s AND merchant_key = %s",
                   (display_name, uid, merchant_key))
    applied = 0
    if data.get("apply_existing") and category_id is not None:
        ids = [r["id"] for r in (db.query(
            """SELECT id FROM transactions WHERE user_id = %s AND merchant_key = %s
               AND (category_id IS DISTINCT FROM %s OR category_status <> 'confirmed')""",
            (uid, merchant_key, category_id)) or [])]
        if ids:
            db.execute(
                """UPDATE transactions SET category_id = %s, category_status = 'confirmed', category_source = 'merchant',
                       category_rule_id = NULL, category_confidence = 0.95, updated_at = now()
                   WHERE id = ANY(%s)""",
                (category_id, ids),
            )
            record_events([(i, "merchant", {"category_id": category_id}, uid) for i in ids])
            applied = len(ids)
    if display_name and data.get("rename_all"):
        db.execute("UPDATE transactions SET merchant_name = %s WHERE user_id = %s AND merchant_key = %s",
                   (display_name, uid, merchant_key))
    audit("merchant.update", {"merchant_key": merchant_key, "applied": applied})
    return jsonify({"ok": True, "applied": applied})


@bp.delete("/<path:merchant_key>")
@login_required
def delete_merchant(merchant_key):
    uid = session["user_id"]
    categorizer.forget(uid, merchant_key)
    audit("merchant.forget", {"merchant_key": merchant_key})
    return jsonify({"ok": True})


@bp.post("/renormalize")
@login_required
def renormalize():
    """Recompute merchant names for every transaction with the current normalizer; rules and
    merchant memory keyed on old names are carried along."""
    import maintenance
    result = maintenance.renormalize_user(session["user_id"])
    audit("merchants.renormalize", result)
    return jsonify(result)


@bp.post("/learn")
@login_required
def learn():
    """Rebuild merchant memory from everything already categorized (safe to run any time)."""
    import maintenance
    result = maintenance.learn_from_history(session["user_id"])
    audit("merchants.learn", result)
    return jsonify(result)
