"""Shared test scaffolding: stub db/config so API modules import without Postgres."""
import os
import sys
import types


class FakeDB:
    """Records SQL and returns queued results. query_results / execute_results are FIFO lists."""

    def __init__(self):
        self.calls = []
        self.query_results = []
        self.execute_results = []
        self.settings = {}

    def query(self, sql, params=None, one=False, commit=True):
        self.calls.append(("query", " ".join(sql.split()), params))
        if self.query_results:
            res = self.query_results.pop(0)
            if callable(res):
                return res(sql, params)
            return res
        return None if one else []

    def execute(self, sql, params=None, returning=False, commit=True):
        self.calls.append(("execute", " ".join(sql.split()), params))
        if self.execute_results:
            res = self.execute_results.pop(0)
            return res(sql, params) if callable(res) else res
        return None if returning else 1

    def execute_values(self, sql, rows, template=None, commit=True, page_size=500):
        self.calls.append(("execute_values", " ".join(sql.split()), list(rows)))

    def transaction(self):
        import contextlib

        @contextlib.contextmanager
        def _tx():
            yield self
        return _tx()

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def user_setting(self, user_id, key, default=None):
        return self.settings.get(f"u{user_id}:{key}", default)

    def set_user_setting(self, user_id, key, value):
        self.settings[f"u{user_id}:{key}"] = value

    def run_migrations(self):
        """create_app() calls this; the stub schema is whatever the tests route."""

    def connect(self):
        return self

    def get_db(self):
        return self

    def close_db(self, _exc=None):
        pass


class _FakeStripeError(Exception):
    pass


def install_stripe():
    """Inject a controllable stripe module BEFORE billing/billing_api are imported."""
    existing = sys.modules.get("stripe")
    if existing is not None and getattr(existing, "_ispend_fake", False):
        return existing
    fake = types.SimpleNamespace(
        _ispend_fake=True, api_key=None, api_version=None, max_network_retries=0,
        VERSION="fake",
        SignatureVerificationError=type("SignatureVerificationError", (Exception,), {}),
        StripeError=_FakeStripeError,
        Webhook=types.SimpleNamespace(construct_event=lambda payload, sig, secret: None),
        Customer=types.SimpleNamespace(create=lambda **kw: {"id": "cus_fake"}),
        Price=types.SimpleNamespace(retrieve=lambda pid: {"unit_amount": 500, "currency": "usd",
                                                          "recurring": {"interval": "month"}}),
        Subscription=types.SimpleNamespace(retrieve=lambda sid: {"id": sid, "status": "active",
                                                                 "items": {"data": [{}]}}),
        checkout=types.SimpleNamespace(
            Session=types.SimpleNamespace(create=lambda **kw: {"url": "https://checkout.test/s"})),
        billing_portal=types.SimpleNamespace(
            Session=types.SimpleNamespace(create=lambda **kw: {"url": "https://portal.test/s"})),
    )
    sys.modules["stripe"] = fake
    return fake


def install(tmp_dir="/tmp"):
    """Install (once) a FakeDB as the `db` module and a config stub. Idempotent: every test file
    that calls this shares the same FakeDB instance, so modules imported by an earlier test file
    keep pointing at the object later files patch. Only STATEMENTS_DIR is updated on re-install."""
    os.environ.setdefault("SECRET_KEY", "test")
    os.environ.setdefault("POSTGRES_PASSWORD", "test")
    existing = sys.modules.get("db")
    # duck-typed: this module is imported both as `_stubs` and `tests._stubs`, giving two FakeDB classes
    if existing is not None and type(existing).__name__ == "FakeDB":
        cfg = sys.modules.get("config")
        if cfg is not None and tmp_dir != "/tmp":
            cfg.STATEMENTS_DIR = tmp_dir
        return existing
    fake = FakeDB()
    sys.modules["db"] = fake
    sys.modules["config"] = types.SimpleNamespace(
        SECRET_KEY="test", ADMIN_INITIAL_PASSWORD="admin", POSTGRES={}, STATEMENTS_DIR=tmp_dir,
        MAX_UPLOAD_BYTES=25 * 1024 * 1024, MAX_RESTORE_BYTES=4 * 1024 ** 3, USE_X_ACCEL=False,
        OCR_TIMEOUT_SECONDS=10, OCR_LANGS="eng",
        OPENROUTER_BASE_URL="http://localhost", OPENROUTER_TIMEOUT=5, AI_BATCH_SIZE=40, APP_TIMEZONE="UTC",
        APP_BASE_URL="http://localhost:5559", STRIPE_SECRET_KEY="", STRIPE_WEBHOOK_SECRET="",
        STRIPE_PRICE_MONTHLY="", STRIPE_PRICE_YEARLY="",
        SMTP_HOST="", SMTP_PORT="", SMTP_SECURITY="", SMTP_USER="", SMTP_PASSWORD="",
        SMTP_FROM_EMAIL="", SMTP_FROM_NAME="",
        SESSION_COOKIE_SECURE=False,
    )
    return fake


class Router:
    """Answer db.query/db.execute by SQL substring instead of FIFO order; records every call.

    routes: list of (substring, result); the first matching substring wins, a callable result
    is called with (sql, params). Unmatched queries return None/[] (or `default`)."""

    def __init__(self, routes=(), default=None):
        self.routes = list(routes)
        self.default = default
        self.calls = []

    def __call__(self, sql, params=None, one=False, returning=False, commit=True):
        flat = " ".join(sql.split())
        self.calls.append((flat, params))
        for needle, result in self.routes:
            if needle in flat:
                return result(flat, params) if callable(result) else result
        if self.default is not None:
            return self.default
        return None if (one or returning) else []

    def sql(self, needle):
        """All recorded (sql, params) whose SQL contains needle."""
        return [(s, p) for s, p in self.calls if needle in s]
