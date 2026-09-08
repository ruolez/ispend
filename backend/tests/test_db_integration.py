"""Opt-in integration suite against a real Postgres (the trigger, ON CONFLICT dedupe and statement discard
cannot be exercised with the stubbed db module).

    ISPEND_TEST_DSN=postgresql://ispend:<password>@postgres/ispend \\
        python -m unittest tests.test_db_integration

Run it on its own: under `discover` the other suites replace the db module with a stub first, and this
file then skips itself. The DSN's role must be allowed to CREATE DATABASE; a scratch database
ispend_it_<pid> is created, migrated and dropped.
"""
import os
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from urllib.parse import urlparse

DSN = os.environ.get("ISPEND_TEST_DSN")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _real_db_available():
    mod = sys.modules.get("db")
    return DSN and (mod is None or type(mod).__name__ != "FakeDB")


class _Upload:
    def __init__(self, path):
        self.filename = os.path.basename(path)
        with open(path, "rb") as f:
            self._data = f.read()

    def read(self):
        return self._data


@unittest.skipUnless(_real_db_available(), "set ISPEND_TEST_DSN and run this file on its own")
class DatabaseIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg2

        u = urlparse(DSN)
        base = {"host": u.hostname, "port": u.port or 5432, "user": u.username, "password": u.password, "dbname": u.path.lstrip("/")}
        cls.scratch = f"ispend_it_{os.getpid()}"
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
        cls.tmp = tempfile.mkdtemp()
        config.STATEMENTS_DIR = cls.tmp
        import db
        from flask import Flask

        cls.db = db
        db.run_migrations()
        cls.app = Flask(__name__)
        cls.ctx = cls.app.app_context()
        cls.ctx.push()
        cls.uid = db.execute("INSERT INTO users (username, password_hash, role) VALUES ('it', 'x', 'user') RETURNING id",
                             returning=True)["id"]
        import seed_categories
        seed_categories.seed_for_user(db.get_db(), cls.uid)
        cls.card = db.execute("INSERT INTO accounts (user_id, name, account_type, currency) VALUES (%s, 'Card', 'credit_card', 'USD') RETURNING id",
                              (cls.uid,), returning=True)["id"]

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

    def _cat(self, slug):
        return self.db.query("SELECT id, kind FROM categories WHERE user_id = %s AND slug = %s", (self.uid, slug), one=True)["id"]

    def _insert_txn(self, amount, category_id=None, status="none", fp="fp"):
        return self.db.execute(
            """INSERT INTO transactions (user_id, account_id, txn_date, amount, currency, description_raw, description_clean,
                   merchant_key, merchant_name, category_id, category_status, fingerprint, occurrence)
               VALUES (%s, %s, %s, %s, 'USD', 'd', 'd', 'k', 'K', %s, %s, %s, 1)
               RETURNING id, is_transfer, is_excluded""",
            (self.uid, self.card, date(2026, 1, 5), Decimal(amount), category_id, status, fp), returning=True)

    def test_transfer_kind_trigger_follows_confirmation(self):
        transfers = self._cat("transfers")
        groceries = self._cat("groceries")
        suggested = self._insert_txn("-50.00", transfers, "suggested", "t1")
        self.assertEqual((suggested["is_transfer"], suggested["is_excluded"]), (False, False))
        self.db.execute("UPDATE transactions SET category_status = 'confirmed' WHERE id = %s", (suggested["id"],))
        row = self.db.query("SELECT is_transfer, is_excluded FROM transactions WHERE id = %s", (suggested["id"],), one=True)
        self.assertEqual((row["is_transfer"], row["is_excluded"]), (True, True))
        self.db.execute("UPDATE transactions SET category_id = %s WHERE id = %s", (groceries, suggested["id"]))
        row = self.db.query("SELECT is_transfer, is_excluded FROM transactions WHERE id = %s", (suggested["id"],), one=True)
        self.assertEqual((row["is_transfer"], row["is_excluded"]), (False, False))
        confirmed = self._insert_txn("-20.00", transfers, "confirmed", "t2")
        self.assertEqual((confirmed["is_transfer"], confirmed["is_excluded"]), (True, True))

    def _rejected(self, fn):
        import psycopg2

        with self.assertRaises(psycopg2.errors.RaiseException) as ctx:
            fn()
        self.db.get_db().rollback()
        return ctx.exception.diag.message_primary

    def test_split_trigger_enforces_count_sign_and_sum(self):
        groceries, shopping = self._cat("groceries"), self._cat("shopping")
        txn = self._insert_txn("-50.00", groceries, "confirmed", "s1")["id"]
        insert = "INSERT INTO transaction_splits (transaction_id, category_id, amount, sort_order) VALUES %s"
        self.db.execute_values(insert, [(txn, groceries, Decimal("-30.00"), 0), (txn, shopping, Decimal("-20.00"), 1)])
        count = lambda: self.db.query("SELECT COUNT(*) AS n FROM transaction_splits WHERE transaction_id = %s", (txn,), one=True)["n"]  # noqa: E731
        self.assertEqual(count(), 2)
        self.assertEqual(self._rejected(lambda: self.db.execute_values(insert, [(txn, shopping, Decimal("-5.00"), 2)])),
                         "Split lines must add up to the transaction amount")
        self.assertEqual(self._rejected(lambda: self.db.execute(
            "DELETE FROM transaction_splits WHERE transaction_id = %s AND sort_order = 1", (txn,))), "A split needs at least two lines")
        self.assertEqual(self._rejected(lambda: self.db.execute("UPDATE transactions SET amount = -60 WHERE id = %s", (txn,))),
                         "Split lines must add up to the transaction amount")
        self.assertEqual(self._rejected(lambda: self.db.execute(
            "UPDATE transaction_splits SET amount = 30 WHERE transaction_id = %s AND sort_order = 0", (txn,))),
            "Split lines must have the same sign as the transaction")
        self.assertEqual(count(), 2)
        import signs

        self.assertEqual(signs.flip_signs(self.uid, [txn]), 1)
        amounts = [r["amount"] for r in self.db.query("SELECT amount FROM transaction_splits WHERE transaction_id = %s ORDER BY sort_order", (txn,))]
        self.assertEqual(amounts, [Decimal("30.00"), Decimal("20.00")])
        self.db.execute("DELETE FROM transaction_splits WHERE transaction_id = %s", (txn,))
        self.assertEqual(count(), 0)

    def test_split_lines_attribute_category_reports_without_changing_totals(self):
        import reports

        groceries, shopping = self._cat("groceries"), self._cat("shopping")
        txn = self._insert_txn("-80.00", groceries, "confirmed", "s2")["id"]
        self.db.execute_values("INSERT INTO transaction_splits (transaction_id, category_id, amount, sort_order) VALUES %s",
                               [(txn, groceries, Decimal("-50.00"), 0), (txn, shopping, Decimal("-30.00"), 1)])
        day = date(2026, 1, 5)
        by_cat = {c["id"]: (c["total"], c["count"]) for c in reports.by_category(self.uid, day, day, level="sub")}
        self.assertEqual((by_cat[groceries], by_cat[shopping]), ((50.0, 1), (30.0, 1)))
        self.assertEqual(reports.summary(self.uid, day, day)["expenses"], 80.0)
        self.db.execute("DELETE FROM transactions WHERE id = %s", (txn,))
        self.assertEqual(self.db.query("SELECT COUNT(*) AS n FROM transaction_splits WHERE transaction_id = %s", (txn,), one=True)["n"], 0)

    def test_reimport_skips_every_row_and_discard_rolls_back(self):
        import importer

        st = importer.store_upload(self.uid, _Upload(os.path.join(FIXTURES, "chase_card.csv")), account_id=self.card)
        importer.parse_statement(st["id"])
        first = importer.commit_statement(st["id"], self.card)
        self.assertGreater(first["imported"], 0)
        self.assertEqual(first["skipped_duplicates"], 0)
        n_rows = self.db.query("SELECT COUNT(*) AS n FROM transactions WHERE statement_id = %s", (st["id"],), one=True)["n"]
        self.assertEqual(n_rows, first["imported"])

        again = importer.store_upload(self.uid, _Upload(os.path.join(FIXTURES, "chase_card.csv")), account_id=self.card)
        self.assertEqual(again["duplicate_of"], st["id"])
        importer.parse_statement(again["id"])
        second = importer.commit_statement(again["id"], self.card)
        self.assertEqual((second["imported"], second["skipped_duplicates"]), (0, first["imported"]))

        deleted = importer.discard_statement(self.db.query("SELECT * FROM statements WHERE id = %s", (st["id"],), one=True),
                                             delete_transactions=True)
        self.assertEqual(deleted, first["imported"])
        left = self.db.query("SELECT COUNT(*) AS n FROM transactions WHERE statement_id = %s", (st["id"],), one=True)["n"]
        self.assertEqual(left, 0)


if __name__ == "__main__":
    unittest.main()
