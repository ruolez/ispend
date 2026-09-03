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

    def get_db(self):
        return self

    def close_db(self, _exc=None):
        pass


def install(tmp_dir="/tmp"):
    os.environ.setdefault("SECRET_KEY", "test")
    os.environ.setdefault("POSTGRES_PASSWORD", "test")
    fake = FakeDB()
    sys.modules["db"] = fake
    sys.modules["config"] = types.SimpleNamespace(
        SECRET_KEY="test", ADMIN_INITIAL_PASSWORD="admin", POSTGRES={}, STATEMENTS_DIR=tmp_dir,
        MAX_UPLOAD_BYTES=25 * 1024 * 1024, OCR_TIMEOUT_SECONDS=10, OCR_LANGS="eng",
        OPENROUTER_BASE_URL="http://localhost", OPENROUTER_TIMEOUT=5, AI_BATCH_SIZE=40, APP_TIMEZONE="UTC",
    )
    return fake
