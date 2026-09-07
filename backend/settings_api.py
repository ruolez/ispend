import os
import shutil

from flask import Blueprint, jsonify, request, session
from werkzeug.security import generate_password_hash

import config
import db
import openrouter
import seed_categories
from auth import MIN_PASSWORD_LEN, admin_required, login_required, password_problem
from util import api_error, audit

bp = Blueprint("settings", __name__, url_prefix="/api")

MASK = "••••••••"
SETTING_KEYS = ["openrouter_api_key", "openrouter_model", "ai_categorize_enabled", "ai_insights_enabled"]
SECRET_KEYS = {"openrouter_api_key"}


def _uid():
    return session["user_id"]


def _is_admin():
    return session.get("role") == "admin"


@bp.get("/settings")
@login_required
def get_settings():
    """The current user's AI settings; `shared_available` tells whether an admin saved a key for everyone."""
    uid = _uid()
    out = {}
    for key in SETTING_KEYS:
        value = db.user_setting(uid, key)
        out[key] = (MASK if value else "") if key in SECRET_KEYS else (value or "")
    out["shared_available"] = bool(db.get_setting("openrouter_api_key")) and bool(db.get_setting("openrouter_model"))
    out["shared_model"] = db.get_setting("openrouter_model") or ""
    out["is_admin"] = _is_admin()
    if _is_admin():
        out["shared_api_key"] = MASK if db.get_setting("openrouter_api_key") else ""
    return jsonify(out)


@bp.put("/settings")
@login_required
def put_settings():
    """Saves the current user's keys. Admins may pass shared=true to also publish the key and
    model for every user who has not configured their own."""
    uid = _uid()
    data = request.get_json(silent=True) or {}
    shared = bool(data.get("shared")) and _is_admin()
    for key, value in data.items():
        if key not in SETTING_KEYS:
            continue
        if key in SECRET_KEYS and value == MASK:
            continue
        if isinstance(value, bool):
            value = "1" if value else "0"
        clean = (str(value) if value is not None else "").strip()
        db.set_user_setting(uid, key, clean)
        if shared and key in ("openrouter_api_key", "openrouter_model"):
            db.set_setting(key, clean)
    if _is_admin() and data.get("clear_shared"):
        db.set_setting("openrouter_api_key", "")
        db.set_setting("openrouter_model", "")
    audit("settings.update", {"keys": [k for k in data if k in SETTING_KEYS], "shared": shared})
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
    data = request.get_json(silent=True) or {}
    key = (data.get("api_key") or "").strip()
    if not key or key == MASK:
        key = None
    model_id = (data.get("model") or "").strip() or None
    try:
        return jsonify(openrouter.test_connection(key=key, model_id=model_id, user_id=_uid()))
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
    if not username or password_problem(password):
        return api_error(f"Username and a password of at least {MIN_PASSWORD_LEN} characters are required")
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
        with db.transaction():
            db.execute("DELETE FROM users WHERE id = %s", (user_id,), commit=False)
            db.execute("DELETE FROM settings WHERE key LIKE %s", (f"u{user_id}:%",), commit=False)
        shutil.rmtree(os.path.join(config.STATEMENTS_DIR, str(user_id)), ignore_errors=True)
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
    problem = password_problem(password)
    if problem:
        return api_error(problem)
    db.execute("UPDATE users SET password_hash = %s WHERE id = %s", (generate_password_hash(password), user_id))
    audit("user.password_reset", {"id": user_id})
    return jsonify({"ok": True})
