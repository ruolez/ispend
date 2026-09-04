import logging

from flask import Blueprint, jsonify, request, session

import categorizer
import db
import rules as rules_mod
from auth import login_required
from util import api_error, audit, parse_int_list, record_events, rows_json

log = logging.getLogger(__name__)

bp = Blueprint("review", __name__, url_prefix="/api/review")

QUEUE_WHERE = "t.user_id = %s AND NOT t.is_transfer AND (t.category_id IS NULL OR t.category_status = 'suggested')"


@bp.get("/count")
@login_required
def count():
    row = db.query(
        """SELECT COUNT(*) FILTER (WHERE category_id IS NULL) AS uncategorized,
                  COUNT(*) FILTER (WHERE category_status = 'suggested') AS suggested,
                  COUNT(*) AS total
           FROM transactions t WHERE """ + QUEUE_WHERE,
        (session["user_id"],), one=True,
    ) or {}
    return jsonify({"uncategorized": row.get("uncategorized", 0), "suggested": row.get("suggested", 0),
                    "total": row.get("total", 0)})


@bp.get("")
@login_required
def queue():
    uid = session["user_id"]
    mode = request.args.get("mode") or "merchant"
    try:
        limit = min(max(int(request.args.get("limit") or 50), 1), 500)
    except ValueError:
        limit = 50
    extra, params = "", [uid]
    account_id = request.args.get("account_id")
    if account_id and account_id.isdigit():
        extra += " AND t.account_id = %s"
        params.append(int(account_id))
    if mode == "single":
        items = db.query(
            f"""SELECT t.* FROM transactions t WHERE {QUEUE_WHERE}{extra}
                ORDER BY t.txn_date DESC, t.id DESC LIMIT %s""",
            params + [limit],
        ) or []
        remaining = (db.query(f"SELECT COUNT(*) AS n FROM transactions t WHERE {QUEUE_WHERE}{extra}", params, one=True) or {}).get("n", 0)
        return jsonify({"items": rows_json([{k: v for k, v in r.items() if k not in ("raw", "fingerprint")} for r in items]),
                        "remaining": remaining})
    groups = db.query(
        f"""SELECT t.merchant_key AS key,
                   MIN(t.merchant_name) AS display,
                   COUNT(*) AS count,
                   SUM(t.amount) AS total,
                   MIN(t.txn_date) AS first,
                   MAX(t.txn_date) AS last,
                   array_agg(t.id ORDER BY t.txn_date DESC, t.id DESC) AS ids,
                   CASE WHEN COUNT(DISTINCT t.category_id) = 1 AND bool_and(t.category_status = 'suggested')
                        THEN MIN(t.category_id) END AS suggestion_id,
                   MAX(t.category_confidence) AS suggestion_confidence,
                   MIN(t.category_source) AS suggestion_source,
                   MIN(t.description_raw) AS sample_description,
                   MODE() WITHIN GROUP (ORDER BY t.currency) AS currency
            FROM transactions t WHERE {QUEUE_WHERE}{extra}
            GROUP BY t.merchant_key
            ORDER BY COUNT(*) DESC, SUM(abs(t.amount)) DESC, t.merchant_key
            LIMIT %s""",
        params + [limit],
    ) or []
    out = []
    for g in rows_json(groups):
        suggestion = None
        if g.get("suggestion_id") is not None:
            suggestion = {"category_id": g["suggestion_id"], "confidence": g.get("suggestion_confidence"),
                          "source": g.get("suggestion_source")}
        out.append({"key": g["key"], "display": g["display"], "count": g["count"], "total": g["total"],
                    "first": g["first"], "last": g["last"], "ids": list(g["ids"] or []),
                    "suggestion": suggestion, "sample_description": g.get("sample_description"),
                    "currency": (g.get("currency") or "USD").strip()})
    totals = db.query(
        f"SELECT COUNT(DISTINCT t.merchant_key) AS groups, COUNT(*) AS items FROM transactions t WHERE {QUEUE_WHERE}{extra}",
        params, one=True,
    ) or {}
    return jsonify({"groups": out, "remaining": totals.get("groups", 0), "remaining_items": totals.get("items", 0)})


@bp.post("/resolve")
@login_required
def resolve():
    uid = session["user_id"]
    data = request.get_json(silent=True) or {}
    ids = parse_int_list(data.get("ids"))
    if not ids:
        return api_error("ids are required")
    own = [r["id"] for r in (db.query(
        "SELECT id FROM transactions WHERE user_id = %s AND id = ANY(%s)", (uid, ids)) or [])]
    if not own:
        return api_error("No matching transactions", 404)
    category_id = data.get("category_id")
    mark_transfer = bool(data.get("mark_transfer"))
    learn = bool(data.get("learn", True))
    if category_id is not None:
        if not db.query("SELECT 1 FROM categories WHERE id = %s AND user_id = %s", (category_id, uid), one=True):
            return api_error("Category not found", 404)
    elif not mark_transfer:
        return api_error("Choose a category or mark as transfer")
    updated = 0
    if mark_transfer:
        if category_id is None:
            for slug in ("transfers.internal", "transfers"):
                row = db.query("SELECT id FROM categories WHERE user_id = %s AND slug = %s", (uid, slug), one=True)
                if row:
                    category_id = row["id"]
                    break
        updated = db.execute(
            """UPDATE transactions SET is_transfer = TRUE, is_excluded = TRUE,
                   category_id = COALESCE(%s, category_id),
                   category_status = CASE WHEN %s IS NULL THEN category_status ELSE 'confirmed' END,
                   category_source = 'manual', category_rule_id = NULL, category_confidence = 1.0, updated_at = now()
               WHERE id = ANY(%s)""",
            (category_id, category_id, own),
        )
        record_events([(i, "transfer", {"is_transfer": True, "category_id": category_id}, uid) for i in own])
        if learn and category_id is not None:
            keys = db.query("SELECT DISTINCT merchant_key, MIN(merchant_name) AS name FROM transactions WHERE id = ANY(%s) GROUP BY merchant_key", (own,)) or []
            for k in keys:
                categorizer.learn(uid, k["merchant_key"], category_id, display_name=k["name"], is_transfer=True)
    else:
        updated = categorizer.apply_manual(uid, own, category_id, learn_memory=learn)

    rule_id = None
    create_rule = data.get("create_rule")
    if isinstance(create_rule, dict) and (create_rule.get("pattern") or "").strip():
        try:
            clean = rules_mod.validate({
                "name": create_rule.get("name"),
                "match_type": create_rule.get("match_type") or "contains",
                "match_field": create_rule.get("match_field") or "description_clean",
                "pattern": create_rule.get("pattern"),
                "category_id": category_id,
                "set_transfer": mark_transfer,
                "account_id": create_rule.get("account_id"),
            })
        except ValueError as e:
            return api_error(str(e))
        top = db.query("SELECT COALESCE(MAX(priority), 0) AS p FROM rules WHERE user_id = %s", (uid,), one=True)
        row = db.execute(
            """INSERT INTO rules (user_id, name, priority, match_type, match_field, pattern, case_sensitive,
                   account_id, category_id, set_transfer, hit_count, last_hit_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id""",
            (uid, clean["name"], (top["p"] if top else 0) + 10, clean["match_type"], clean["match_field"],
             clean["pattern"], clean["case_sensitive"], clean["account_id"], clean["category_id"],
             clean["set_transfer"], len(own)),
            returning=True,
        )
        rule_id = row["id"]
        db.execute("UPDATE transactions SET category_rule_id = %s WHERE id = ANY(%s)", (rule_id, own))
    audit("review.resolve", {"count": updated, "category_id": category_id, "rule_id": rule_id, "transfer": mark_transfer})
    return jsonify({"updated": updated, "rule_id": rule_id})


@bp.post("/suggest")
@login_required
def suggest():
    uid = session["user_id"]
    data = request.get_json(silent=True) or {}
    ids = parse_int_list(data.get("ids"))
    try:
        import openrouter
        if not openrouter.enabled("categorize", uid):
            return api_error("AI categorization is not enabled. Configure OpenRouter in Settings.", 502)
        import ai_categorizer
    except ImportError:
        return api_error("AI categorization is not available", 502)
    if not ids:
        rows = db.query(
            "SELECT t.id FROM transactions t WHERE t.user_id = %s AND t.category_id IS NULL AND NOT t.is_transfer ORDER BY t.txn_date DESC LIMIT 40",
            (uid,),
        ) or []
        ids = [r["id"] for r in rows]
    if not ids:
        return jsonify({"suggested": [], "count": 0})
    try:
        result = ai_categorizer.suggest_sync(uid, ids)
    except openrouter.OpenRouterError as e:
        return api_error(str(e), 502)
    except Exception:
        log.exception("AI suggestion failed")
        return api_error("AI suggestion failed; check the server log", 502)
    audit("review.suggest", {"count": len(ids)})
    return jsonify(result)
