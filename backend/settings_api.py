from flask import Blueprint, jsonify, request, session
from werkzeug.security import generate_password_hash

import db
import openrouter
import seed_categories
from auth import admin_required, login_required
from util import api_error, audit

bp = Blueprint("settings", __name__, url_prefix="/api")

MASK = "••••••••"
SETTING_KEYS = ["openrouter_api_key", "openrouter_model", "ai_categorize_enabled", "ai_insights_enabled"]
SECRET_KEYS = {"openrouter_api_key"}


@bp.get("/settings")
@admin_required
def get_settings():
    out = {}
    for key in SETTING_KEYS:
        value = db.get_setting(key)
        out[key] = (MASK if value else "") if key in SECRET_KEYS else (value or "")
    return jsonify(out)


@bp.put("/settings")
@admin_required
def put_settings():
    data = request.get_json(silent=True) or {}
    for key, value in data.items():
        if key not in SETTING_KEYS:
            continue
        if key in SECRET_KEYS and value == MASK:
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        db.set_setting(key, (str(value) if value is not None else "").strip())
    audit("settings.update", {"keys": [k for k in data if k in SETTING_KEYS]})
    return jsonify({"ok": True})


@bp.get("/settings/client")
@login_required
def client_settings():
    """Non-secret settings any logged-in user's UI needs."""
    configured = bool(db.get_setting("openrouter_api_key")) and bool(db.get_setting("openrouter_model"))
    return jsonify({
        "ai_configured": configured,
        "ai_categorize_enabled": configured and db.get_setting("ai_categorize_enabled") == "1",
        "ai_insights_enabled": configured and db.get_setting("ai_insights_enabled") == "1",
        "ai_model": db.get_setting("openrouter_model") or "",
    })


@bp.get("/settings/openrouter/models")
@admin_required
def openrouter_models():
    try:
        return jsonify(openrouter.list_models(force=request.args.get("refresh") == "1"))
    except openrouter.OpenRouterError as e:
        return api_error(str(e), 502)


@bp.post("/settings/openrouter/test")
@admin_required
def openrouter_test():
    data = request.get_json(silent=True) or {}
    key = (data.get("api_key") or "").strip()
    if not key or key == MASK:
        key = None
    model_id = (data.get("model") or "").strip() or None
    try:
        return jsonify(openrouter.test_connection(key=key, model_id=model_id))
    except openrouter.OpenRouterError as e:
        return api_error(str(e))


# ---------- Users ----------

@bp.get("/users")
@admin_required
def list_users():
    rows = db.query(
        """SELECT u.id, u.username, u.role, u.is_active, u.created_at,
                  (SELECT COUNT(*) FROM transactions t WHERE t.user_id = u.id) AS txn_count,
                  (SELECT COUNT(*) FROM accounts a WHERE a.user_id = u.id) AS account_count
           FROM users u ORDER BY u.id"""
    )
    return jsonify([{**r, "created_at": r["created_at"].isoformat()} for r in rows])


@bp.post("/users")
@admin_required
def create_user():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = data.get("role") if data.get("role") in ("admin", "user") else "user"
    if not username or len(password) < 6:
        return api_error("Username and a password of at least 6 characters are required")
    existing = db.query("SELECT id FROM users WHERE username = %s", (username,), one=True)
    if existing:
        return api_error("Username already exists")
    row = db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING id",
        (username, generate_password_hash(password), role),
        returning=True,
    )
    seed_categories.seed_for_user(db.get_db(), row["id"])
    audit("user.create", {"username": username, "role": role})
    return jsonify({"id": row["id"]}), 201


@bp.put("/users/<int:user_id>")
@admin_required
def update_user(user_id):
    data = request.get_json(silent=True) or {}
    row = db.query("SELECT id FROM users WHERE id = %s", (user_id,), one=True)
    if not row:
        return api_error("User not found", 404)
    if "role" in data:
        if data["role"] not in ("admin", "user"):
            return api_error("Invalid role")
        if user_id == session["user_id"] and data["role"] != "admin":
            return api_error("You cannot remove your own admin role")
        db.execute("UPDATE users SET role = %s WHERE id = %s", (data["role"], user_id))
    if "is_active" in data:
        if user_id == session["user_id"] and not data["is_active"]:
            return api_error("You cannot deactivate your own account")
        db.execute("UPDATE users SET is_active = %s WHERE id = %s", (bool(data["is_active"]), user_id))
    audit("user.update", {"id": user_id, "fields": [k for k in data if k in ("role", "is_active")]})
    return jsonify({"ok": True})


@bp.delete("/users/<int:user_id>")
@admin_required
def delete_user(user_id):
    if user_id == session["user_id"]:
        return api_error("You cannot delete your own account")
    if request.args.get("permanent") == "true":
        db.execute("DELETE FROM users WHERE id = %s", (user_id,))
        audit("user.delete", {"id": user_id})
    else:
        db.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (user_id,))
        audit("user.deactivate", {"id": user_id})
    return jsonify({"ok": True})


@bp.put("/users/<int:user_id>/password")
@admin_required
def reset_password(user_id):
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    if len(password) < 6:
        return api_error("Password must be at least 6 characters")
    db.execute("UPDATE users SET password_hash = %s WHERE id = %s", (generate_password_hash(password), user_id))
    audit("user.password_reset", {"id": user_id})
    return jsonify({"ok": True})
