import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import parse_qs

from flask import Blueprint, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import config
import db
import entitlement
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
# Deliberately loose: an address that round-trips a confirmation link is the only real proof, and
# a stricter pattern only rejects valid addresses.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")
MAX_EMAIL_LEN = 254
RESET_TTL = timedelta(minutes=60)
VERIFY_TTL = timedelta(days=7)
MAX_RESET_TOKENS_PER_HOUR = 5
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
        """SELECT u.id, u.role, u.status, u.session_epoch, u.email, u.email_verified_at,
                  u.created_at,
                  (SELECT value FROM settings WHERE key = 'session_epoch') AS global_epoch,
                  s.status AS sub_status, s.plan, s.price_id, s.stripe_customer_id,
                  s.stripe_subscription_id, s.trial_end, s.current_period_end,
                  s.cancel_at_period_end, s.lapsed_at, s.grace_until, s.comped_until, s.synced_at
             FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id
            WHERE u.id = %s""", (uid,), one=True)
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
    # Per-user epoch: bumped on a password change, so a stolen cookie dies with the password.
    # Flask sessions are stateless, so without this a reset does not sign the thief out.
    if session.get("uepoch", 0) != (row.get("session_epoch") or 0):
        session.clear()
        return
    g.user_row = row
    g.entitlement = entitlement.evaluate(row)
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


def _by_identity(identity):
    """Username first, deterministically, then email. Signup sets username = email, so the two
    only ever disagree for accounts that predate email."""
    user = db.query("SELECT * FROM users WHERE username = %s", (identity,), one=True)
    if user is None and "@" in (identity or ""):
        user = db.query("SELECT * FROM users WHERE lower(email) = lower(%s)", (identity,), one=True)
    return user


def identity_taken(value, exclude_user_id=None):
    """An email that collides with somebody's username would make _by_identity ambiguous."""
    row = db.query(
        """SELECT id FROM users
            WHERE (username = %(v)s OR lower(email) = lower(%(v)s))
              AND (%(exclude)s::int IS NULL OR id <> %(exclude)s)
            LIMIT 1""", {"v": value, "exclude": exclude_user_id}, one=True)
    return row["id"] if row else None


def email_problem(email):
    if not email or len(email) > MAX_EMAIL_LEN or not EMAIL_RE.match(email):
        return "Enter a valid email address"
    return None


def _maybe_refresh_subscription(user_id):
    try:
        import billing_api
        row = db.query("SELECT id, stripe_subscription_id, synced_at FROM subscriptions "
                       "WHERE user_id = %s", (user_id,), one=True)
        if row:
            billing_api.maybe_refresh({**row, "id": user_id})
    except Exception:
        pass   # billing is optional; never let it break a login


def _issue_token(user_id, kind, ttl):
    """256 bits of CSPRNG output, stored only as SHA-256. The plaintext exists just long enough
    to be put in an email."""
    token = secrets.token_urlsafe(32)
    db.execute(
        """INSERT INTO auth_tokens (user_id, kind, token_hash, expires_at, created_ip)
           VALUES (%s, %s, %s, %s, %s)""",
        (user_id, kind, hashlib.sha256(token.encode()).hexdigest(),
         datetime.now(timezone.utc) + ttl, request.remote_addr))
    return token


def _consume_token(token, kind):
    """Single use, enforced atomically: a read-then-write pair lets two requests redeem the same
    link."""
    row = db.execute(
        """UPDATE auth_tokens SET used_at = now()
            WHERE token_hash = %s AND kind = %s AND used_at IS NULL AND expires_at > now()
            RETURNING user_id""",
        (hashlib.sha256((token or "").encode()).hexdigest(), kind), returning=True)
    return row["user_id"] if row else None


def _base_url():
    import billing
    try:
        return billing.base_url()
    except Exception:
        return (config.APP_BASE_URL or request.url_root).rstrip("/")


def signup_enabled():
    return db.get_setting("signup_enabled") == "1"


def _me_payload(user):
    ent = entitlement.evaluate(user) if "sub_status" in (user or {}) else None
    return {
        "id": user["id"],
        "username": user["username"],
        "email": user.get("email"),
        "email_verified": bool(user.get("email_verified_at")),
        "role": user["role"],
        "preferences": user.get("preferences") or {},
        "billing": entitlement.public_json(ent) if ent else None,
    }


@bp.post("/login")
def login():
    data = json_body()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = _by_identity(username)
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
    session["uepoch"] = user.get("session_epoch") or 0
    db.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))
    audit("auth.login")
    _maybe_refresh_subscription(user["id"])
    return with_theme_cookie(jsonify(_me_payload(user)), (user.get("preferences") or {}).get("theme"))


@bp.post("/logout")
def logout():
    session.clear()
    return with_theme_cookie(jsonify({"ok": True}), None)


@bp.get("/me")
@login_required
def me():
    user = db.query(
        """SELECT u.*, s.status AS sub_status, s.plan, s.trial_end, s.current_period_end,
                  s.cancel_at_period_end, s.lapsed_at, s.grace_until, s.comped_until,
                  s.stripe_subscription_id
             FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id
            WHERE u.id = %s""", (session["user_id"],), one=True)
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
    row = db.execute(
        """UPDATE users SET password_hash = %s, session_epoch = session_epoch + 1,
                            updated_at = now()
            WHERE id = %s RETURNING session_epoch, email, username""",
        (generate_password_hash(new), session["user_id"]), returning=True)
    # Every other session dies; this one is re-stamped so the user is not signed out of the tab
    # they just used.
    session["uepoch"] = row["session_epoch"]
    audit("user.password_change", {"id": session["user_id"]})
    if row.get("email"):
        import mailer
        mailer.send_async("password_changed", row["email"], username=row["username"])
    return jsonify({"ok": True})


# ---------- Public signup, verification and password reset ----------

@bp.get("/public-config")
def public_config():
    """Lets the login page decide whether to offer "Create one" without exposing anything."""
    import billing
    return jsonify({"signup_enabled": signup_enabled(), "billing_enabled": billing.enabled()})


@bp.post("/signup")
def signup():
    import mailer
    import seed_categories

    if not signup_enabled():
        return jsonify({"error": "Sign-ups are closed on this server.",
                        "code": "signup_disabled"}), 403
    data = json_body()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    problem = email_problem(email) or password_problem(password)
    if problem:
        return api_error(problem)
    if identity_taken(email):
        # Deliberately distinguishable. Full enumeration resistance would cost auto-login and a
        # redirect-to-inbox in the funnel, to hide something login timing largely leaks anyway;
        # the nginx rate limit caps the abuse.
        return jsonify({"error": "That email already has an account. Sign in instead.",
                        "code": "email_taken"}), 409
    trial = entitlement.trial_days()
    with db.transaction():
        user = db.execute(
            """INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)
               RETURNING *""",
            (email, email, generate_password_hash(password)), returning=True, commit=False)
        db.execute(
            """INSERT INTO subscriptions (user_id, status, trial_end)
               VALUES (%s, 'trialing', now() + make_interval(days => %s))""",
            (user["id"], trial), commit=False)
        seed_categories.seed_for_user(db.get_db(), user["id"])
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["epoch"] = db.get_setting("session_epoch") or ""
    session["uepoch"] = 0
    audit("auth.signup", {"id": user["id"]}, user_id=user["id"])
    token = _issue_token(user["id"], "verify", VERIFY_TTL)
    trial_end = (datetime.now(timezone.utc) + timedelta(days=trial)).strftime("%d %B %Y")
    mailer.send_async("welcome", email, username=email, trial_end=trial_end,
                      link=f"{_base_url()}/verify.html?token={token}")
    row = db.query(
        """SELECT u.*, s.status AS sub_status, s.trial_end, s.current_period_end,
                  s.cancel_at_period_end, s.grace_until, s.comped_until, s.stripe_subscription_id
             FROM users u LEFT JOIN subscriptions s ON s.user_id = u.id WHERE u.id = %s""",
        (user["id"],), one=True)
    return with_theme_cookie(jsonify(_me_payload(row)), None), 201


@bp.post("/password/forgot")
def forgot_password():
    """Always 200, unconditionally: the response must not reveal whether the address exists. The
    send is spawned, so the latency is identical either way."""
    import mailer

    email = (json_body().get("email") or "").strip().lower()
    user = db.query("SELECT id, username, email FROM users WHERE lower(email) = lower(%s) "
                    "AND status = 'active'", (email,), one=True)
    if user:
        recent = db.query(
            """SELECT COUNT(*) AS n FROM auth_tokens
                WHERE user_id = %s AND kind = 'reset' AND created_at > now() - interval '1 hour'""",
            (user["id"],), one=True)
        if recent["n"] < MAX_RESET_TOKENS_PER_HOUR:
            token = _issue_token(user["id"], "reset", RESET_TTL)
            audit("auth.forgot", {"id": user["id"]}, user_id=user["id"])
            mailer.send_async("password_reset", user["email"], username=user["username"],
                              link=f"{_base_url()}/reset.html?token={token}")
    return jsonify({"ok": True})


@bp.post("/password/reset")
def reset_password_with_token():
    data = json_body()
    new = data.get("password") or ""
    problem = password_problem(new, "New password")
    if problem:
        return api_error(problem)
    user_id = _consume_token(data.get("token"), "reset")
    if not user_id:
        return api_error("That link has expired or was already used. Request a new one.")
    with db.transaction():
        user = db.execute(
            """UPDATE users SET password_hash = %s, session_epoch = session_epoch + 1,
                                email_verified_at = COALESCE(email_verified_at, now()),
                                updated_at = now()
                WHERE id = %s RETURNING *""",
            (generate_password_hash(new), user_id), returning=True, commit=False)
        # Any other outstanding reset link for this account is now void.
        db.execute("DELETE FROM auth_tokens WHERE user_id = %s AND kind = 'reset' AND used_at IS NULL",
                   (user_id,), commit=False)
    if not user_state.can_sign_in(user):
        return api_error(user_state.BLOCKED_MESSAGE, 403)
    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["role"] = user["role"]
    session["epoch"] = db.get_setting("session_epoch") or ""
    session["uepoch"] = user["session_epoch"]
    audit("auth.password_reset", {"id": user_id}, user_id=user_id)
    return with_theme_cookie(jsonify(_me_payload(user)), (user.get("preferences") or {}).get("theme"))


@bp.post("/email/verify")
def verify_email():
    user_id = _consume_token(json_body().get("token"), "verify")
    if not user_id:
        return api_error("That link has expired or was already used.")
    db.execute("UPDATE users SET email_verified_at = now(), updated_at = now() WHERE id = %s",
               (user_id,))
    audit("auth.email_verified", {"id": user_id}, user_id=user_id)
    return jsonify({"ok": True})


@bp.post("/email/resend")
@login_required
def resend_verification():
    import mailer

    user = db.query("SELECT id, username, email, email_verified_at FROM users WHERE id = %s",
                    (session["user_id"],), one=True)
    if not user.get("email"):
        return api_error("Add an email address first")
    if user.get("email_verified_at"):
        return jsonify({"ok": True})
    recent = db.query(
        """SELECT COUNT(*) AS n FROM auth_tokens WHERE user_id = %s AND kind = 'verify'
            AND created_at > now() - interval '5 minutes'""", (user["id"],), one=True)
    if not recent["n"]:
        token = _issue_token(user["id"], "verify", VERIFY_TTL)
        mailer.send_async("verify_email", user["email"], username=user["username"],
                          link=f"{_base_url()}/verify.html?token={token}")
    return jsonify({"ok": True})


@bp.put("/me/email")
@login_required
def update_email():
    """How the seeded `admin` account gains a reset-capable address."""
    import mailer

    data = json_body()
    email = (data.get("email") or "").strip().lower()
    problem = email_problem(email)
    if problem:
        return api_error(problem)
    user = db.query("SELECT id, username, password_hash FROM users WHERE id = %s",
                    (session["user_id"],), one=True)
    if not check_password_hash(user["password_hash"], str(data.get("current_password") or "")):
        return api_error("Current password is incorrect")
    if identity_taken(email, exclude_user_id=user["id"]):
        return jsonify({"error": "That email is already in use.", "code": "email_taken"}), 409
    db.execute("UPDATE users SET email = %s, email_verified_at = NULL, updated_at = now() "
               "WHERE id = %s", (email, user["id"]))
    audit("user.email_change", {"id": user["id"]})
    token = _issue_token(user["id"], "verify", VERIFY_TTL)
    mailer.send_async("verify_email", email, username=user["username"],
                      link=f"{_base_url()}/verify.html?token={token}")
    return jsonify({"ok": True, "email": email})
