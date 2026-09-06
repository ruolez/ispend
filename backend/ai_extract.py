"""Read transactions out of statement text with the configured OpenRouter model.

A fallback for layouts the line parser cannot handle: the page text (from pdfplumber or OCR)
is sent page by page; the model returns rows as JSON which are staged like parsed rows.
"""
import json
import logging
import time
from datetime import date
from decimal import Decimal, InvalidOperation

import openrouter
from importer.models import ParsedRow

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You extract bank or credit card transactions from statement text.
Return ONLY a JSON object: {"rows": [{"date": "YYYY-MM-DD", "description": "...", "amount": -12.34, "balance": 100.00}]}.
Rules: amount is negative for money leaving the account (purchases, withdrawals, fees, card charges) and
positive for money coming in (deposits, payments received, refunds, credits). Include every transaction line,
one row each, with the full description (merchant, counterparty, reference). Omit balances if not shown.
Skip headers, footers, totals, beginning/ending balances, summaries and worksheets. If the statement period is
given, use it to resolve dates that lack a year. Never invent rows: if there are none, return {"rows": []}."""


def _to_amount(v):
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _to_date(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def extract_rows(user_id, page_texts, period=None, model_id=None):
    """page_texts: list of (page_number, text). Returns (rows: list[ParsedRow], calls: int)."""
    rows, calls = [], 0
    hint = f"Statement period: {period[0].isoformat()} to {period[1].isoformat()}.\n" if period else ""
    for page_no, text in page_texts:
        text = (text or "").strip()
        if len(text) < 20:
            continue
        started = time.time()
        try:
            parsed, usage = openrouter.chat_json(SYSTEM_PROMPT, f"{hint}Page {page_no}:\n{text[:12000]}",
                                                 model_id=model_id, max_tokens=8000, user_id=user_id, timeout=120)
            calls += 1
            openrouter.log_call(user_id, "categorize", model_id or openrouter.model(user_id), 0, usage, "ok",
                                duration_ms=int((time.time() - started) * 1000))
        except openrouter.OpenRouterError as e:
            openrouter.log_call(user_id, "categorize", model_id or openrouter.model(user_id), 0, None, "error",
                                error=str(e), duration_ms=int((time.time() - started) * 1000))
            raise
        for i, r in enumerate(parsed.get("rows") or []):
            if not isinstance(r, dict):
                continue
            d, amt = _to_date(r.get("date")), _to_amount(r.get("amount"))
            desc = " ".join(str(r.get("description") or "").split())
            if not desc:
                continue
            problems = []
            if d is None:
                problems.append("AI could not read the date")
            if amt is None:
                problems.append("AI could not read the amount")
            rows.append(ParsedRow(row_index=page_no * 1000 + i, txn_date=d, posted_date=None, description=desc,
                                  amount=amt, balance=_to_amount(r.get("balance")) if r.get("balance") is not None else None,
                                  raw={"page": page_no, "source": "ai", "line": json.dumps(r, default=str)[:400]},
                                  problems=problems))
    return rows, calls
