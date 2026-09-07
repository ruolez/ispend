import json
from functools import wraps

from flask import Blueprint, jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

import config
import db
from util import api_error, audit, json_body, to_int

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

PREFERENCE_KEYS = {"theme", "density", "default_account_id", "currency", "week_start"}
PREFERENCE_CHOICES = {"theme": ("system", "light", "dark"), "density": ("comfortable", "compact"), "currency": ("", "USD", "CAD")}
MIN_PASSWORD_LEN = 10
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def clean_preferences(data):
    """Known keys only, each validated; None clears a preference. ValueError carries the message."""
    out = {}
    for key, value in data.items():
        if key not in PREFERENCE_KEYS:
            continue
        if value is None:
            out[key] = None
        elif key in PREFERENCE_CHOICES:
            if not isinstance(value, str) or value not in PREFERENCE_CHOICES[key]:
                raise ValueError(f"Invalid {key}")
            out[key] = value
        elif key == "default_account_id":
            out[key] = to_int(value, "default_account_id")
        elif key == "week_start":
            out[key] = to_int(value, "week_start", lo=0, hi=6)
    return out


def password_problem(password, label="Password"):
    """User-facing message when a new password is unacceptable, else None."""
    if len(password or "") < MIN_PASSWORD_LEN:
        return f"{label} must be at least {MIN_PASSWORD_LEN} characters"
    return None


def refresh_session_user():
    """before_request hook: the signed cookie only proves who logged in; the account row decides
    what it may do now. Deactivated or deleted users lose the session, role changes apply at once."""
    uid = session.get("user_id")
    if uid is None:
        return
    row = db.query("SELECT role, is_active FROM users WHERE id = %s", (uid,), one=True)
    if not row or not row["is_active"]:
        session.clear()
        return
    if session.get("role") != row["role"]:
        session["role"] = row["role"]


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


THEME_COOKIE = "ispend_theme"


def with_theme_cookie(response, theme):
    """A readable (non-HttpOnly) cookie lets the head script paint the saved theme before /api/auth/me."""
    if theme in ("light", "dark", "system"):
        response.set_cookie(THEME_COOKIE, theme, max_age=60 * 60 * 24 * 365, samesite="Lax",
                            secure=config.SESSION_COOKIE_SECURE, httponly=False)
    else:
        response.delete_cookie(THEME_COOKIE, samesite="Lax", secure=config.SESSION_COOKIE_SECURE)
    return response


def _me_payload(user):
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "preferences": user.get("preferences") or {},
    }


@bp.post("/login")
def login():
    data = json_body()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = db.query("SELECT * FROM users WHERE username = %s", (username,), one=True)
    usable = bool(user and user["is_active"])
    ok = check_password_hash(user["password_hash"] if usable else _DUMMY_HASH, str(password))
    if not (usable and ok):
        return api_error("Invalid username or password", 401)
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    audit("auth.login")
    return with_theme_cookie(jsonify(_me_payload(user)), (user.get("preferences") or {}).get("theme"))


@bp.post("/logout")
def logout():
    session.clear()
    return with_theme_cookie(jsonify({"ok": True}), None)


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
    data = json_body()
    try:
        clean = clean_preferences(data)
    except ValueError as e:
        return api_error(str(e))
    user = db.query("SELECT preferences FROM users WHERE id = %s", (session["user_id"],), one=True)
    prefs = {**(user["preferences"] or {}), **clean}
    db.execute("UPDATE users SET preferences = %s WHERE id = %s", (json.dumps(prefs), session["user_id"]))
    response = jsonify(prefs)
    return with_theme_cookie(response, prefs.get("theme")) if "theme" in clean else response


@bp.put("/me/password")
@login_required
def change_own_password():
    data = json_body()
    current = data.get("current_password") or ""
    new = data.get("password") or ""
    user = db.query("SELECT password_hash FROM users WHERE id = %s", (session["user_id"],), one=True)
    if not check_password_hash(user["password_hash"], str(current)):
        return api_error("Current password is incorrect")
    problem = password_problem(new, "New password")
    if problem:
        return api_error(problem)
    db.execute(
        "UPDATE users SET password_hash = %s WHERE id = %s",
        (generate_password_hash(new), session["user_id"]),
    )
    audit("user.password_change", {"id": session["user_id"]})
    return jsonify({"ok": True})
