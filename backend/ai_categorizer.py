"""AI category suggestions via OpenRouter. Never confirms: rows land as category_status='suggested'."""
import json
import logging
import time

import config
import db
import openrouter
from openrouter import OpenRouterError
from util import record_events

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a personal-finance assistant that assigns spending categories to bank and credit-card "
    "transactions. Use the merchant name and description to pick the single best category id from the "
    "provided list. Prefer subcategories when one clearly fits. If nothing fits, omit the item. "
    "Reply with JSON only: {\"items\":[{\"i\":<row index>,\"category_id\":<id>,\"confidence\":<0-1>,"
    "\"reason\":\"<max 8 words>\"}]}"
)


def user_categories(uid):
    rows = db.query(
        """SELECT c.id, c.name, c.kind, p.name AS parent_name
           FROM categories c LEFT JOIN categories p ON p.id = c.parent_id
           WHERE c.user_id = %s ORDER BY c.parent_id NULLS FIRST, c.sort_order, c.name""",
        (uid,),
    )
    return [{"id": r["id"], "path": f"{r['parent_name']} > {r['name']}" if r["parent_name"] else r["name"],
             "kind": r["kind"]} for r in rows]


def build_prompt(rows, cats):
    items = [{"i": i, "description": r["description_clean"], "merchant": r["merchant_name"],
              "amount": float(r["amount"]), "date": r["txn_date"].isoformat() if hasattr(r["txn_date"], "isoformat") else str(r["txn_date"])}
             for i, r in enumerate(rows)]
    return (
        "Categories (id: path):\n"
        + "\n".join(f"{c['id']}: {c['path']}" for c in cats)
        + "\n\nTransactions (negative amount = money out):\n"
        + json.dumps(items, ensure_ascii=False)
    )


def _load_rows(uid, txn_ids):
    if not txn_ids:
        return []
    return db.query(
        """SELECT id, description_clean, merchant_name, amount, txn_date
           FROM transactions
           WHERE user_id = %s AND id = ANY(%s) AND category_id IS NULL AND NOT is_transfer
           ORDER BY id""",
        (uid, list(txn_ids)),
    )


def _call(uid, rows, cats):
    """One OpenRouter call for <= AI_BATCH_SIZE rows -> list of (row, category_id, confidence, reason)."""
    valid = {c["id"] for c in cats}
    model_id = openrouter.model(uid)
    started = time.time()
    try:
        parsed, usage = openrouter.chat_json(SYSTEM_PROMPT, build_prompt(rows, cats), max_tokens=200 * len(rows) + 1500,
                                             user_id=uid)
    except OpenRouterError as e:
        openrouter.log_call(uid, "categorize", model_id, len(rows), None, "error", str(e),
                            int((time.time() - started) * 1000))
        raise
    openrouter.log_call(uid, "categorize", model_id, len(rows), usage, "ok", None,
                        int((time.time() - started) * 1000))
    out = []
    for item in (parsed.get("items") if isinstance(parsed, dict) else None) or []:
        try:
            i = int(item.get("i"))
            cid = int(item.get("category_id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not (0 <= i < len(rows)) or cid not in valid:
            continue
        try:
            conf = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        reason = str(item.get("reason") or "")[:200]
        out.append((rows[i], cid, conf, reason))
    return out


def _apply(uid, suggestions):
    applied = []
    events = []
    for row, cid, conf, reason in suggestions:
        n = db.execute(
            """UPDATE transactions
               SET category_id = %s, category_status = 'suggested', category_source = 'ai',
                   category_confidence = %s, ai_rationale = %s, updated_at = now()
               WHERE id = %s AND user_id = %s AND category_id IS NULL AND NOT is_transfer""",
            (cid, round(conf, 3), reason or None, row["id"], uid),
        )
        if n:
            applied.append({"id": row["id"], "category_id": cid, "confidence": round(conf, 3), "reason": reason})
            events.append((row["id"], "ai", {"category_id": cid, "confidence": round(conf, 3), "reason": reason}, uid))
    record_events(events)
    return applied


def suggest_for_ids(uid, txn_ids):
    """Background job: categorize in batches; per-batch failures are logged and skipped."""
    if not openrouter.enabled("categorize", uid):
        return {"suggested": 0, "skipped": "disabled"}
    rows = _load_rows(uid, txn_ids)
    if not rows:
        return {"suggested": 0, "skipped": "nothing to do"}
    cats = user_categories(uid)
    if not cats:
        return {"suggested": 0, "skipped": "no categories"}
    size = max(1, int(config.AI_BATCH_SIZE))
    total, errors = 0, 0
    for i in range(0, len(rows), size):
        batch = rows[i:i + size]
        try:
            total += len(_apply(uid, _call(uid, batch, cats)))
        except OpenRouterError as e:
            errors += 1
            log.warning("AI categorization batch failed for user %s: %s", uid, e)
    return {"suggested": total, "batches": (len(rows) + size - 1) // size, "errors": errors}


def suggest_sync(uid, txn_ids):
    """Synchronous variant for the UI (<= AI_BATCH_SIZE rows). Raises OpenRouterError."""
    if not openrouter.enabled("categorize", uid):
        raise OpenRouterError("AI categorization is not enabled. Add an OpenRouter key and model in Settings.")
    rows = _load_rows(uid, txn_ids[: max(1, int(config.AI_BATCH_SIZE))])
    if not rows:
        return {"suggested": 0, "items": []}
    cats = user_categories(uid)
    if not cats:
        raise OpenRouterError("No categories exist to suggest from")
    items = _apply(uid, _call(uid, rows, cats))
    return {"suggested": len(items), "items": items}
