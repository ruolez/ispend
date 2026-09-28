"""Export and erasure against a real database: the archive holds everything, and afterwards nothing
of the person is left but bookkeeping.

    ISPEND_TEST_DSN=postgresql://... python -m unittest tests.test_privacy_integration
"""
import json
import os
import sys
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _pg  # noqa: E402


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class PrivacyTest(_pg.PgTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import config
        db = cls.db
        uid = db.execute("INSERT INTO users (username, email, password_hash) VALUES ('pat', 'pat@example.com', 'x') RETURNING id",
                         returning=True)["id"]
        cls.uid = uid
        import seed_categories
        seed_categories.seed_for_user(db.get_db(), uid)
        acct = db.execute("INSERT INTO accounts (user_id, name, account_type, currency) VALUES (%s, 'Joint', 'checking', 'USD') RETURNING id",
                          (uid,), returning=True)["id"]
        folder = os.path.join(config.STATEMENTS_DIR, str(uid))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "abc.csv"), "w") as f:
            f.write("date,amount\n2026-01-01,-5\n")
        st = db.execute("""INSERT INTO statements (user_id, account_id, original_filename, stored_path, file_sha256, file_size, file_kind, status)
                           VALUES (%s, %s, 'march.csv', %s, 'abc', 30, 'csv', 'committed') RETURNING id""",
                        (uid, acct, f"{uid}/abc.csv"), returning=True)["id"]
        db.execute("""INSERT INTO transactions (user_id, account_id, statement_id, txn_date, amount, currency, description_raw,
                                                description_clean, merchant_key, merchant_name, fingerprint, occurrence)
                      VALUES (%s, %s, %s, '2026-01-01', -5, 'USD', 'COFFEE', 'Coffee', 'coffee', 'Coffee', 'fp', 1)""",
                   (uid, acct, st))
        db.execute("INSERT INTO settings (key, value) VALUES (%s, 'sk-secret'), (%s, 'm/a')",
                   (f"u{uid}:openrouter_api_key", f"u{uid}:openrouter_model"))
        db.execute("INSERT INTO audit_log (user_id, action, detail) VALUES (%s, 'account.create', %s)",
                   (uid, json.dumps({"id": acct, "name": "Joint"})))
        db.execute("""INSERT INTO payments (user_id, stripe_invoice_id, status, currency, amount_paid_cents, paid_at)
                      VALUES (%s, 'in_pat', 'paid', 'usd', 999, now())""", (uid,))
        db.execute("""INSERT INTO subscription_events (user_id, source, kind, mrr_to_cents, occurred_at)
                      VALUES (%s, 'webhook', 'subscribed', 999, now())""", (uid,))
        db.execute("INSERT INTO login_events (user_id, kind, ok) VALUES (%s, 'password', true)", (uid,))
        db.execute("INSERT INTO user_activity_days (user_id, day, kinds) VALUES (%s, current_date, 2)", (uid,))
        db.execute("INSERT INTO subscriptions (user_id, status) VALUES (%s, 'trialing')", (uid,))

    def test_1_export_contains_everything_and_hides_secrets(self):
        import privacy
        export_id = self.db.execute("INSERT INTO data_exports (user_id, token) VALUES (%s, 'tok') RETURNING id",
                                    (self.uid,), returning=True)["id"]
        with mock.patch("mailer.send_template"):
            privacy.build_export(export_id)
        row = self.db.query("SELECT status, file_path, size_bytes FROM data_exports WHERE id = %s", (export_id,), one=True)
        self.assertEqual(row["status"], "done")
        with zipfile.ZipFile(row["file_path"]) as z:
            names = set(z.namelist())
            self.assertTrue({"README.txt", "transactions.csv", "settings.json", "transactions.json",
                             f"statements/statement-{self._statement_id()}.csv"} <= names)
            self.assertEqual({f"{t}.json" for t in privacy.EXPORTED} - names, set())
            settings = json.loads(z.read("settings.json"))
            self.assertEqual(settings, {"openrouter_api_key": "(hidden)", "openrouter_model": "m/a"})
            self.assertIn("Coffee", z.read("transactions.csv").decode())
        self.assertEqual(oct(os.stat(row["file_path"]).st_mode & 0o777), "0o600")

    def _statement_id(self):
        return self.db.query("SELECT id FROM statements WHERE user_id = %s", (self.uid,), one=True)["id"]

    def test_2_erase_leaves_only_bookkeeping(self):
        import config
        import privacy
        with mock.patch("mailer.send_async") as send:
            record = privacy.erase(self.uid, "self")
        self.assertEqual(record["counts"]["transactions"], 1)
        self.assertEqual(send.call_args.args[:2], ("account_deleted", "pat@example.com"))
        tables = self.db.query(
            """SELECT table_name FROM information_schema.columns
                WHERE table_schema = current_schema() AND column_name = 'user_id'""")
        left = {}
        for t in tables:
            n = self.db.query(f'SELECT COUNT(*) AS n FROM "{t["table_name"]}" WHERE user_id = %s', (self.uid,), one=True)["n"]
            if n:
                left[t["table_name"]] = n
        self.assertEqual(left, {"payments": 1, "subscription_events": 1})
        self.assertEqual(self.db.query("SELECT COUNT(*) AS n FROM settings WHERE key LIKE %s", (f"u{self.uid}:%",), one=True)["n"], 0)
        detail = self.db.query("SELECT detail FROM audit_log WHERE action = 'account.create'", one=True)["detail"]
        self.assertNotIn("name", detail, "names are scrubbed from the activity log")
        self.assertFalse(os.path.exists(os.path.join(config.STATEMENTS_DIR, str(self.uid))))
        erasure = self.db.query("SELECT requested_by, email_sha256, files_removed FROM erasures WHERE erased_user_id = %s",
                                (self.uid,), one=True)
        self.assertEqual((erasure["requested_by"], len(erasure["email_sha256"]), erasure["files_removed"] >= 2),
                         ("self", 64, True))


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class WipeOwnedDataTest(_pg.PgTestCase):
    """An admin account that held finance data from before keeps its sign-in and history."""

    def test_wipe_removes_the_finance_data_and_keeps_the_account(self):
        import config
        import privacy
        import seed_categories
        db = self.db
        uid = db.execute("INSERT INTO users (username, password_hash, role) VALUES ('boss', 'x', 'admin') RETURNING id",
                         returning=True)["id"]
        seed_categories.seed_for_user(db.get_db(), uid)
        acct = db.execute("INSERT INTO accounts (user_id, name, account_type, currency) VALUES (%s, 'Mine', 'checking', 'USD') RETURNING id",
                          (uid,), returning=True)["id"]
        cat = db.query("SELECT id FROM categories WHERE user_id = %s AND parent_id IS NOT NULL LIMIT 1", (uid,), one=True)["id"]
        st = db.execute("""INSERT INTO statements (user_id, account_id, original_filename, stored_path, file_sha256, file_size, file_kind, status)
                           VALUES (%s, %s, 'may.csv', %s, 'def', 30, 'csv', 'committed') RETURNING id""",
                        (uid, acct, f"{uid}/def.csv"), returning=True)["id"]
        db.execute("""INSERT INTO transactions (user_id, account_id, statement_id, txn_date, amount, currency, description_raw,
                                                description_clean, merchant_key, merchant_name, fingerprint, occurrence, category_id)
                      VALUES (%s, %s, %s, '2026-02-01', -9, 'USD', 'TEA', 'Tea', 'tea', 'Tea', 'fp2', 1, %s)""",
                   (uid, acct, st, cat))
        db.execute("INSERT INTO rules (user_id, name, match_type, pattern, category_id) VALUES (%s, 'tea', 'contains', 'TEA', %s)",
                   (uid, cat))
        db.execute("INSERT INTO settings (key, value) VALUES (%s, 'sk-own')", (f"u{uid}:openrouter_api_key",))
        db.execute("INSERT INTO login_events (user_id, kind, ok) VALUES (%s, 'password', true)", (uid,))
        folder = os.path.join(config.STATEMENTS_DIR, str(uid))
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "def.csv"), "w") as f:
            f.write("date,amount\n")

        removed = privacy.wipe_owned_data(uid)

        self.assertEqual({k: removed[k] for k in ("transactions", "statements", "accounts", "rules")},
                         {"transactions": 1, "statements": 1, "accounts": 1, "rules": 1})
        self.assertEqual(privacy.owned_data_counts(uid), dict.fromkeys(removed, 0))
        left = {
            "user": db.query("SELECT COUNT(*) AS n FROM users WHERE id = %s", (uid,), one=True)["n"],
            "logins": db.query("SELECT COUNT(*) AS n FROM login_events WHERE user_id = %s", (uid,), one=True)["n"],
            "own_settings": db.query("SELECT COUNT(*) AS n FROM settings WHERE key LIKE %s", (f"u{uid}:%",), one=True)["n"],
        }
        self.assertEqual(left, {"user": 1, "logins": 1, "own_settings": 0})
        self.assertFalse(os.path.exists(folder))


if __name__ == "__main__":
    unittest.main()
