"""OpenRouter client (OpenAI-compatible chat completions + public model list)."""
import json
import re
import threading
import time

import requests

import config
import db


class OpenRouterError(Exception):
    pass


_models_cache = {"at": 0.0, "data": None}
_models_lock = threading.Lock()
MODELS_TTL = 600


def _user_or_global(user_id, key):
    """A user's own value first, then the shared value an admin may have saved for everyone."""
    if user_id is not None:
        v = (db.get_setting(f"u{user_id}:{key}") or "").strip()
        if v:
            return v
    return (db.get_setting(key) or "").strip()


def api_key(user_id=None):
    return _user_or_global(user_id, "openrouter_api_key")


def model(user_id=None):
    return _user_or_global(user_id, "openrouter_model")


def configured(user_id=None):
    return bool(api_key(user_id)) and bool(model(user_id))


def enabled(purpose, user_id=None):
    key = "ai_categorize_enabled" if purpose == "categorize" else "ai_insights_enabled"
    flag = _user_or_global(user_id, key)
    return configured(user_id) and flag == "1"


STATUS_MESSAGES = {
    401: "Invalid API key: OpenRouter rejected the credentials",
    402: "Insufficient credits on your OpenRouter account",
    403: "OpenRouter refused the request (key lacks access to this model)",
    404: "Model not found on OpenRouter; pick another model",
    429: "Rate limited by OpenRouter; try again in a moment",
}


def _headers(key=None, user_id=None):
    return {
        "Authorization": f"Bearer {key or api_key(user_id)}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/ruolez/ispend",
        "X-OpenRouter-Title": "iSpend",
    }


def list_models(force=False):
    """Public catalog; cached in-process for 10 minutes."""
    with _models_lock:
        if not force and _models_cache["data"] and time.time() - _models_cache["at"] < MODELS_TTL:
            return _models_cache["data"]
    try:
        resp = requests.get(f"{config.OPENROUTER_BASE_URL}/models", timeout=20)
    except requests.RequestException as e:
        raise OpenRouterError(f"Could not reach OpenRouter: {e}")
    if resp.status_code != 200:
        raise OpenRouterError(f"OpenRouter returned {resp.status_code}")
    out = []
    for m in resp.json().get("data") or []:
        pricing = m.get("pricing") or {}
        try:
            prompt_price = float(pricing.get("prompt") or 0) * 1_000_000
            completion_price = float(pricing.get("completion") or 0) * 1_000_000
        except (TypeError, ValueError):
            prompt_price = completion_price = None
        params = m.get("supported_parameters") or []
        out.append({
            "id": m.get("id"),
            "name": m.get("name") or m.get("id"),
            "context_length": m.get("context_length"),
            "prompt_price": prompt_price,
            "completion_price": completion_price,
            "structured": "response_format" in params or "structured_outputs" in params,
            "created": m.get("created"),
        })
    out.sort(key=lambda x: (x["id"] or ""))
    with _models_lock:
        _models_cache.update(at=time.time(), data=out)
    return out


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def chat_json(system, user, model_id=None, max_tokens=2000, temperature=0.1, timeout=None, key=None, user_id=None):
    """Chat completion that must return a JSON object. Returns (parsed, usage)."""
    model_id = model_id or model(user_id)
    if not (key or api_key(user_id)) or not model_id:
        raise OpenRouterError("OpenRouter API key and model are not configured")
    body = {
        "model": model_id,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    try:
        resp = requests.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers=_headers(key, user_id), json=body, timeout=timeout or config.OPENROUTER_TIMEOUT,
        )
    except requests.RequestException as e:
        raise OpenRouterError(f"OpenRouter request failed: {e}")
    try:
        data = resp.json()
    except ValueError:
        raise OpenRouterError(f"OpenRouter returned non-JSON ({resp.status_code})")
    if resp.status_code != 200 or (isinstance(data, dict) and "error" in data):
        err = data.get("error") if isinstance(data, dict) else None
        code = resp.status_code
        if isinstance(err, dict) and isinstance(err.get("code"), int):
            code = err["code"]
        if code in STATUS_MESSAGES:
            raise OpenRouterError(STATUS_MESSAGES[code])
        msg = err.get("message") if isinstance(err, dict) else str(err or resp.text[:200])
        raise OpenRouterError(f"OpenRouter error {code}: {msg}")
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise OpenRouterError("OpenRouter response had no content")
    text = _FENCE.sub("", (content or "").strip())
    try:
        parsed = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise OpenRouterError("Model did not return JSON")
        try:
            parsed = json.loads(m.group(0))
        except ValueError:
            raise OpenRouterError("Model returned malformed JSON")
    return parsed, data.get("usage") or {}


def test_connection(key=None, model_id=None, user_id=None):
    started = time.time()
    parsed, usage = chat_json(
        "Reply with a JSON object {\"ok\": true}.", "ping", model_id=model_id, max_tokens=20, key=key, timeout=30,
        user_id=user_id,
    )
    return {"ok": True, "model": model_id or model(user_id), "latency_ms": int((time.time() - started) * 1000),
            "usage": usage, "reply": parsed}


def log_call(user_id, purpose, model_id, item_count, usage, status, error=None, duration_ms=None):
    db.execute(
        """INSERT INTO ai_calls (user_id, purpose, model, item_count, prompt_tokens, completion_tokens,
                                 status, error_message, duration_ms)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (user_id, purpose, model_id or "", item_count, (usage or {}).get("prompt_tokens"),
         (usage or {}).get("completion_tokens"), status, error, duration_ms),
    )
