import json
from functools import wraps

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import db
from util import api_error, audit

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

PREFERENCE_KEYS = {"theme", "density", "default_account_id", "currency", "week_start"}


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return api_error("Not authenticated", 401)
        return f(*args, **kwargs)

    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return api_error("Not authenticated", 401)
        if session.get("role") != "admin":
            return api_error("Admin access required", 403)
        return f(*args, **kwargs)

    return wrapper


def current_user_id():
    return session["user_id"]


def _me_payload(user):
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "preferences": user.get("preferences") or {},
    }


@bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = db.query(
        "SELECT * FROM users WHERE username = %s AND is_active", (username,), one=True
    )
    if not user or not check_password_hash(user["password_hash"], password):
        return api_error("Invalid username or password", 401)
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    audit("auth.login")
    return jsonify(_me_payload(user))


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.get("/me")
@login_required
def me():
    user = db.query("SELECT * FROM users WHERE id = %s", (session["user_id"],), one=True)
    if not user or not user["is_active"]:
        session.clear()
        return api_error("Not authenticated", 401)
    return jsonify(_me_payload(user))


@bp.put("/me/preferences")
@login_required
def update_preferences():
    data = request.get_json(silent=True) or {}
    clean = {k: v for k, v in data.items() if k in PREFERENCE_KEYS}
    user = db.query("SELECT preferences FROM users WHERE id = %s", (session["user_id"],), one=True)
    prefs = {**(user["preferences"] or {}), **clean}
    db.execute("UPDATE users SET preferences = %s WHERE id = %s", (json.dumps(prefs), session["user_id"]))
    return jsonify(prefs)


@bp.put("/me/password")
@login_required
def change_own_password():
    data = request.get_json(silent=True) or {}
    current = data.get("current_password") or ""
    new = data.get("password") or ""
    user = db.query("SELECT password_hash FROM users WHERE id = %s", (session["user_id"],), one=True)
    if not check_password_hash(user["password_hash"], current):
        return api_error("Current password is incorrect")
    if len(new) < 6:
        return api_error("New password must be at least 6 characters")
    db.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (generate_password_hash(new), session["user_id"]),
    )
    audit("user.password_change", {"id": session["user_id"]})
    return jsonify({"ok": True})
