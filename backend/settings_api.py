from flask import Blueprint, jsonify, request, session

import db
import openrouter
from auth import login_required
from util import api_error, audit, json_body

bp = Blueprint("settings", __name__, url_prefix="/api")

MASK = "••••••••"
SETTING_KEYS = ["openrouter_api_key", "openrouter_model", "ai_categorize_enabled", "ai_insights_enabled"]
SECRET_KEYS = {"openrouter_api_key"}


def _uid():
    return session["user_id"]


@bp.get("/settings")
@login_required
def get_settings():
    """The current user's AI settings; `shared_available` tells whether the instance has a key for everyone."""
    uid = _uid()
    out = {}
    for key in SETTING_KEYS:
        value = db.user_setting(uid, key)
        out[key] = (MASK if value else "") if key in SECRET_KEYS else (value or "")
    out["shared_available"] = bool(db.get_setting("openrouter_api_key")) and bool(db.get_setting("openrouter_model"))
    out["shared_model"] = db.get_setting("openrouter_model") or ""
    return jsonify(out)


@bp.put("/settings")
@login_required
def put_settings():
    """Saves the current user's keys. The instance key is the admin console's (admin_api)."""
    uid = _uid()
    data = json_body()
    for key, value in data.items():
        if key not in SETTING_KEYS:
            continue
        if key in SECRET_KEYS and value == MASK:
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        clean = (str(value) if value is not None else "").strip()
        db.set_user_setting(uid, key, clean)
    audit("settings.update", {"keys": [k for k in data if k in SETTING_KEYS]})
    return jsonify({"ok": True})


@bp.get("/settings/client")
@login_required
def client_settings():
    """Non-secret settings the current user's UI needs."""
    uid = _uid()
    return jsonify({
        "ai_configured": openrouter.configured(uid),
        "ai_categorize_enabled": openrouter.enabled("categorize", uid),
        "ai_insights_enabled": openrouter.enabled("insights", uid),
        "ai_model": openrouter.model(uid),
    })


@bp.get("/settings/openrouter/models")
@login_required
def openrouter_models():
    try:
        return jsonify(openrouter.list_models(force=request.args.get("refresh") == "1"))
    except openrouter.OpenRouterError as e:
        return api_error(str(e), 502)


@bp.post("/settings/openrouter/test")
@login_required
def openrouter_test():
    data = json_body()
    key = (data.get("api_key") or "").strip()
    if not key or key == MASK:
        key = None
    model_id = (data.get("model") or "").strip() or None
    try:
        return jsonify(openrouter.test_connection(key=key, model_id=model_id, user_id=_uid()))
    except openrouter.OpenRouterError as e:
        return api_error(str(e))
