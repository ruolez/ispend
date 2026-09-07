"""AI-written monthly insights, built from the report aggregates and cached per period."""
import json
import time

import db
import openrouter
import reports
from openrouter import OpenRouterError

SYSTEM_PROMPT = (
    "You are a friendly, practical personal-finance advisor. You receive a JSON summary of one person's "
    "spending for a period. Write specific, actionable observations grounded ONLY in the numbers given; "
    "never invent figures. Mention amounts with two decimals and category or merchant names as given. "
    "Reply with JSON only: {\"summary\": \"<2-3 sentence overview>\", \"insights\": [{\"title\": \"<short>\", "
    "\"body\": \"<1-3 sentences>\", \"severity\": \"info|good|warn\", \"category\": \"<category or merchant name or null>\"}]} "
    "with 4 to 8 insights ordered by importance."
)


def build_context(uid, start, end, account_ids=None):
    cur = reports.summary(uid, start, end, account_ids)
    length = (end - start).days + 1
    prev_end = start.__class__.fromordinal(start.toordinal() - 1)
    prev_start = prev_end.__class__.fromordinal(prev_end.toordinal() - length + 1)
    prev = reports.summary(uid, prev_start, prev_end, account_ids)
    cats = reports.by_category(uid, start, end, "top", account_ids)[:10]
    mom = reports.month_over_month(uid, f"{start.year:04d}-{start.month:02d}", account_ids)
    merchants = reports.top_merchants(uid, start, end, 10, account_ids)
    rec = [s for s in reports.recurring(uid) if s["is_active"]][:10]
    anomalies = reports.anomalies(uid)[:8]
    big = reports.largest(uid, start, end, 5, account_ids)
    return {
        "period": {"start": start.isoformat(), "end": end.isoformat(), "days": length},
        "totals": {"income": cur["income"], "expenses": cur["expenses"], "net": cur["net"],
                   "transactions": cur["txn_count"], "uncategorized": cur["uncategorized"]},
        "previous_period": {"income": prev["income"], "expenses": prev["expenses"], "net": prev["net"]},
        "by_category": [{"name": c["name"], "total": c["total"], "pct": c["pct"], "count": c["count"]} for c in cats],
        "month_over_month": [{"name": c["name"], "current": c["current"], "previous": c["previous"],
                              "delta": c["delta"], "pct": c["pct"]} for c in mom["categories"][:10]],
        "top_merchants": [{"name": m["merchant_name"], "total": m["total"], "count": m["count"]} for m in merchants],
        "recurring": [{"name": s["merchant_name"], "cadence": s["cadence"], "amount": s["median_amount"],
                       "monthly_equivalent": s["monthly_equivalent"], "next_expected": s["next_expected"]} for s in rec],
        "anomalies": [{"kind": a["kind"], "merchant": a["merchant_name"], "amount": a["amount"],
                       "date": a["txn_date"], "text": a.get("text")} for a in anomalies],
        "largest_purchases": [{"merchant": b["merchant_name"], "amount": -b["amount"], "date": b["txn_date"],
                               "category": b.get("category_name")} for b in big],
    }


def _row_payload(row, cached):
    content = row["content"]
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
    except ValueError:
        parsed = {"summary": content, "insights": []}
    created = row["created_at"]
    return {"content": parsed, "model": row["model"],
            "created_at": created.isoformat() if hasattr(created, "isoformat") else created, "cached": cached}


def _data_changed_since(uid, start, end, since):
    """True when a transaction in the period was added or edited after the insight was written."""
    if since is None:
        return False
    row = db.query(
        """SELECT GREATEST(MAX(created_at), MAX(updated_at)) AS latest FROM transactions
           WHERE user_id = %s AND txn_date BETWEEN %s AND %s""",
        (uid, start, end), one=True,
    )
    latest = row and row.get("latest")
    return bool(latest and latest > since)


def cached(uid, start, end):
    row = db.query(
        "SELECT content, model, created_at FROM insights WHERE user_id = %s AND period_start = %s AND period_end = %s",
        (uid, start, end), one=True,
    )
    if not row or _data_changed_since(uid, start, end, row.get("created_at")):
        return None
    return _row_payload(row, True)


def _validate(parsed):
    if not isinstance(parsed, dict):
        raise OpenRouterError("Model returned an unexpected shape")
    items = parsed.get("insights")
    if not isinstance(items, list):
        raise OpenRouterError("Model returned no insights")
    clean = []
    for it in items:
        if not isinstance(it, dict) or not it.get("title"):
            continue
        sev = it.get("severity") if it.get("severity") in ("info", "good", "warn") else "info"
        clean.append({"title": str(it.get("title"))[:120], "body": str(it.get("body") or "")[:1000],
                      "severity": sev, "category": it.get("category")})
    return {"summary": str(parsed.get("summary") or "")[:2000], "insights": clean}


def generate(uid, start, end, force=False, account_ids=None):
    if not force:
        hit = cached(uid, start, end)
        if hit:
            return hit
    if not openrouter.enabled("insights", uid):
        raise OpenRouterError("AI insights are not enabled. Add an OpenRouter key and model in Settings.")
    context = build_context(uid, start, end, account_ids)
    model_id = openrouter.model(uid)
    started = time.time()
    try:
        parsed, usage = openrouter.chat_json(SYSTEM_PROMPT, json.dumps(context, default=str), max_tokens=5000,
                                             temperature=0.4, user_id=uid)
        clean = _validate(parsed)
    except OpenRouterError as e:
        openrouter.log_call(uid, "insights", model_id, 1, None, "error", str(e), int((time.time() - started) * 1000))
        raise
    openrouter.log_call(uid, "insights", model_id, 1, usage, "ok", None, int((time.time() - started) * 1000))
    content = json.dumps(clean)
    row = db.execute(
        """INSERT INTO insights (user_id, period_start, period_end, model, content)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (user_id, period_start, period_end)
           DO UPDATE SET model = EXCLUDED.model, content = EXCLUDED.content, created_at = now()
           RETURNING content, model, created_at""",
        (uid, start, end, model_id, content), returning=True,
    )
    return _row_payload(row or {"content": content, "model": model_id, "created_at": None}, False)
