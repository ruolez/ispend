import json
import re
from functools import wraps
from urllib.parse import parse_qs

from flask import Blueprint, g, jsonify, session
from werkzeug.security import check_password_hash, generate_password_hash

import config
import db
import user_state
from util import api_error, audit, json_body, to_int

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

PREFERENCE_KEYS = {"theme", "density", "default_account_id", "currency", "week_start", "saved_views", "review_skips", "onboarding"}
TX_VIEW_KEYS = {"range", "from", "to", "acct", "cat", "status", "flow", "q", "sort", "min", "max", "transfers", "merchant_key", "tag", "tag_mode", "split"}
VIEW_ID = re.compile(r"^[a-z0-9_-]{1,16}$")
MAX_SAVED_VIEWS = 20
MAX_REVIEW_SKIPS = 300


def _clean_saved_views(value):
    """[{id, name, query, pinned, page}] for the Transactions page; the query is a filter string only."""
    if not isinstance(value, list):
        raise ValueError("saved_views must be a list")
    if len(value) > MAX_SAVED_VIEWS:
        raise ValueError(f"At most {MAX_SAVED_VIEWS} saved views")
    out, seen = [], set()
    for v in value:
        if not isinstance(v, dict):
            raise ValueError("Each saved view must be an object")
        vid = v.get("id")
        if not isinstance(vid, str) or not VIEW_ID.match(vid):
            raise ValueError("Invalid view id")
        if vid in seen:
            raise ValueError("Duplicate view id")
        seen.add(vid)
        name = str(v.get("name") or "").strip()
        if not 1 <= len(name) <= 60:
            raise ValueError("View name must be 1-60 characters")
        query = v.get("query")
        if not isinstance(query, str) or not query.startswith("?") or len(query) > 600:
            raise ValueError("View query must be a short filter string")
        bad = set(parse_qs(query[1:], keep_blank_values=True)) - TX_VIEW_KEYS
        if bad:
            raise ValueError(f"View query has unsupported keys: {', '.join(sorted(bad))}")
        page = v.get("page") or "transactions"
        if page != "transactions":
            raise ValueError("Unsupported view page")
        out.append({"id": vid, "name": name, "query": query, "pinned": bool(v.get("pinned")), "page": page})
    return out


def _clean_review_skips(value):
    if not isinstance(value, list):
        raise ValueError("review_skips must be a list")
    out = []
    for k in value:
        if not isinstance(k, str) or not k or len(k) > 120:
            raise ValueError("Invalid review skip key")
        if k not in out:
            out.append(k)
    return out[:MAX_REVIEW_SKIPS]


def _clean_onboarding(value):
    if not isinstance(value, dict):
        raise ValueError("onboarding must be an object")
    return {k: bool(value.get(k)) for k in ("dismissed", "reports_opened") if k in value}


PREFERENCE_CLEANERS = {"saved_views": _clean_saved_views, "review_skips": _clean_review_skips, "onboarding": _clean_onboarding}
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
        elif key in PREFERENCE_CLEANERS:
            out[key] = PREFERENCE_CLEANERS[key](value)
    return out


def password_problem(password, label="Password"):
    """User-facing message when a new password is unacceptable, else None."""
    if len(password or "") < MIN_PASSWORD_LEN:
        return f"{label} must be at least {MIN_PASSWORD_LEN} characters"
    return None


def refresh_session_user():
    """before_request hook: the signed cookie only proves who logged in; the account row decides
    what it may do now. Locked or deleted users lose the session, role changes apply at once."""
    uid = session.get("user_id")
    if uid is None:
        return
    row = db.query(
        """SELECT u.id, u.role, u.status,
                  (SELECT value FROM settings WHERE key = 'session_epoch') AS global_epoch
             FROM users u WHERE u.id = %s""", (uid,), one=True)
    if not user_state.can_sign_in(row):
        session.clear()
        return
    # A restore replaces the users table while SECRET_KEY stays put, so an old cookie carrying
    # user_id 1 would silently bind to whoever is id 1 in the restored data. The restore sets an
    # epoch; every cookie minted before it then fails this check.
    # Only enforced once an epoch exists, so upgrading an install that has never been restored
    # does not sign everybody out.
    epoch = row.get("global_epoch")
    if epoch and session.get("epoch") != epoch:
        session.clear()
        return
    g.user_row = row
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
    # The dummy compare keeps an unknown username as slow as a known one.
    ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, str(password))
    if not (user and ok):
        return api_error("Invalid username or password", 401)
    if not user_state.can_sign_in(user):
        # Only someone who already proved the password gets to learn the account is blocked, and
        # locked and deleted read identically so the two cannot be told apart.
        audit("auth.login.blocked", {"status": user["status"]}, user_id=user["id"])
        return api_error(user_state.BLOCKED_MESSAGE, 403)
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["epoch"] = db.get_setting("session_epoch") or ""
    db.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))
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
    if not user_state.can_sign_in(user):
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
