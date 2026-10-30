from flask import Blueprint, jsonify, request, session

import ai_categorizer
import config
import db
import insights
import jobs
import openrouter
import reports
from auth import login_required
from openrouter import OpenRouterError
from scope import current_profile_id, current_scope
from util import api_error, audit, json_body, parse_int_list, row_json

bp = Blueprint("ai", __name__, url_prefix="/api")


def _uid():
    return session["user_id"]


# ---------- Categorization ----------

@bp.post("/ai/categorize")
@login_required
def categorize():
    data = json_body()
    if not openrouter.enabled("categorize", _uid()):
        return api_error("AI categorization is not enabled. Add an OpenRouter key and model in Settings.", 502)
    ids = parse_int_list(data.get("ids"))
    if not ids and data.get("scope") == "uncategorized":
        rows = db.query(
            "SELECT id FROM transactions WHERE profile_id = %s AND category_id IS NULL AND NOT is_transfer ORDER BY txn_date DESC",
            (current_profile_id(),),
        )
        ids = [r["id"] for r in rows]
    if not ids:
        return jsonify({"suggested": 0, "items": []})
    if len(ids) <= config.AI_BATCH_SIZE:
        try:
            result = ai_categorizer.suggest_sync(current_scope(), ids)
        except OpenRouterError as e:
            return api_error(str(e), 502)
        audit("ai.categorize", {"count": result["suggested"]})
        return jsonify(result)
    jobs.spawn(ai_categorizer.suggest_for_ids, current_scope(), ids)
    audit("ai.categorize.queued", {"count": len(ids)})
    return jsonify({"queued": len(ids)}), 202


@bp.get("/ai/status")
@login_required
def status():
    uid = _uid()
    pending = db.query(
        "SELECT COUNT(*) AS n FROM transactions WHERE profile_id = %s AND category_status = 'suggested'",
        (current_profile_id(),), one=True,
    )["n"]
    last = db.query(
        """SELECT purpose, model, item_count, status, error_message, duration_ms, created_at
           FROM ai_calls WHERE user_id = %s ORDER BY id DESC LIMIT 1""",
        (uid,), one=True,
    )
    today = db.query(
        """SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) + COALESCE(SUM(completion_tokens), 0) AS tokens
           FROM ai_calls WHERE user_id = %s AND created_at >= date_trunc('day', now())""",
        (uid,), one=True,
    )
    return jsonify({
        "enabled": openrouter.enabled("categorize", uid),
        "insights_enabled": openrouter.enabled("insights", uid),
        "configured": openrouter.configured(uid),
        "model": openrouter.model(uid),
        "pending_suggestions": pending,
        "last_call": row_json(last) if last else None,
        "calls_today": today["calls"],
        "tokens_today": int(today["tokens"] or 0),
    })


# ---------- Insights ----------

def _period():
    y, m = reports.parse_month(request.args.get("month") or json_body().get("month"))
    start, end = reports.month_bounds(y, m)
    return start, end


def _ai_block(scope, start, end):
    uid = scope.uid
    hit = insights.cached(scope.pid, start, end)
    if hit:
        return {"status": "ready", **hit}
    if not openrouter.enabled("insights", uid):
        return {"status": "disabled", "content": None, "model": None, "created_at": None}
    return {"status": "none", "content": None, "model": openrouter.model(uid), "created_at": None}


@bp.get("/insights")
@login_required
def get_insights():
    scope = current_scope()
    start, end = _period()
    return jsonify({
        "period": {"start": start.isoformat(), "end": end.isoformat(), "month": f"{start.year:04d}-{start.month:02d}"},
        "recurring": reports.recurring(scope.pid),
        "anomalies": reports.anomalies(scope.pid),
        "ai": _ai_block(scope, start, end),
    })


@bp.post("/insights/generate")
@login_required
def generate_insights():
    data = json_body()
    y, m = reports.parse_month(data.get("month"))
    start, end = reports.month_bounds(y, m)
    try:
        result = insights.generate(current_scope(), start, end, force=bool(data.get("force")))
    except OpenRouterError as e:
        return api_error(str(e), 502)
    audit("insights.generate", {"month": f"{y:04d}-{m:02d}", "cached": result["cached"]})
    return jsonify({"ai": {"status": "ready", **result}})


@bp.post("/insights/anomalies/<int:txn_id>/dismiss")
@login_required
def dismiss_anomaly(txn_id):
    owned = db.query("SELECT 1 FROM transactions WHERE id = %s AND profile_id = %s", (txn_id, current_profile_id()), one=True)
    if not owned:
        return api_error("Transaction not found", 404)
    db.execute(
        "INSERT INTO anomaly_dismissals (user_id, transaction_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (_uid(), txn_id),
    )
    audit("anomaly.dismiss", {"transaction_id": txn_id})
    return jsonify({"ok": True})


@bp.delete("/insights/anomalies/<int:txn_id>/dismiss")
@login_required
def undismiss_anomaly(txn_id):
    # A dismissal follows its transaction, so the profile is the transaction's.
    db.execute("""DELETE FROM anomaly_dismissals d USING transactions t
                  WHERE t.id = d.transaction_id AND d.transaction_id = %s AND t.profile_id = %s""",
               (txn_id, current_profile_id()))
    return jsonify({"ok": True})
