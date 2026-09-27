"""A scratch Postgres database for opt-in integration suites.

    ISPEND_TEST_DSN=postgresql://ispend:<password>@postgres/ispend \\
        python -m unittest tests.test_admin_metrics_integration

Run such a file on its own: under `discover` the other suites install the stub db module first and
the suite skips itself. The DSN's role must be allowed to CREATE DATABASE; a scratch database is
created, migrated and dropped.
"""
import os
import sys
import tempfile
import unittest
from urllib.parse import urlparse

DSN = os.environ.get("ISPEND_TEST_DSN")


def available():
    mod = sys.modules.get("db")
    return bool(DSN) and (mod is None or type(mod).__name__ != "FakeDB")


class PgTestCase(unittest.TestCase):
    """Subclasses get cls.db (the real db module) inside a pushed app context."""

    @classmethod
    def setUpClass(cls):
        import psycopg2

        u = urlparse(DSN)
        base = {"host": u.hostname, "port": u.port or 5432, "user": u.username, "password": u.password,
                "dbname": u.path.lstrip("/")}
        cls.scratch = f"ispend_it_{cls.__name__.lower()}_{os.getpid()}"
        admin = psycopg2.connect(**base)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{cls.scratch}"')
        admin.close()
        cls.admin_params = base
        os.environ.setdefault("SECRET_KEY", "integration")
        os.environ["POSTGRES_PASSWORD"] = base["password"] or ""
        import config
        config.POSTGRES = {**base, "dbname": cls.scratch}
        config.STATEMENTS_DIR = tempfile.mkdtemp()
        import db
        from flask import Flask

        cls.db = db
        db.run_migrations()
        cls.app = Flask(__name__)
        cls.ctx = cls.app.app_context()
        cls.ctx.push()

    @classmethod
    def tearDownClass(cls):
        import psycopg2

        cls.db.close_db()
        cls.ctx.pop()
        admin = psycopg2.connect(**cls.admin_params)
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{cls.scratch}" WITH (FORCE)')
        admin.close()
