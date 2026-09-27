"""Admin › System SQL against a real database.

    ISPEND_TEST_DSN=postgresql://... python -m unittest tests.test_admin_system_integration
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import _pg  # noqa: E402


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class SystemSqlTest(_pg.PgTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db = cls.db
        uid = db.execute("INSERT INTO users (username, password_hash) VALUES ('sys', 'x') RETURNING id", returning=True)["id"]
        cls.uid = uid
        acct = db.execute("INSERT INTO accounts (user_id, name, account_type, currency) VALUES (%s, 'A', 'checking', 'USD') RETURNING id",
                          (uid,), returning=True)["id"]
        rows = [("stuck", "parsing", "45 minutes", 900), ("fresh", "parsing", "2 minutes", None),
                ("bad", "error", "1 hour", 120), ("ok", "committed", "2 hours", 300)]
        cls.ids = {}
        for name, status, age, ms in rows:
            cls.ids[name] = db.execute(
                f"""INSERT INTO statements (user_id, account_id, original_filename, stored_path, file_sha256, file_size,
                                            file_kind, status, bank_profile, parse_ms, error_message, created_at, updated_at)
                    VALUES (%s, %s, %s, 'x', %s, 10, 'csv', %s, 'chase', %s, %s, now() - interval '{age}', now() - interval '{age}')
                    RETURNING id""",
                (uid, acct, f"{name}.csv", name, status, ms, "Could not read" if status == "error" else None),
                returning=True)["id"]
        db.execute("""INSERT INTO app_errors (source, fingerprint, error_type, message, location)
                      VALUES ('request', 'fp1', 'KeyError', 'x', 'GET /api/x'), ('request', 'fp1', 'KeyError', 'y', 'GET /api/x')""")
        db.execute("""INSERT INTO ai_calls (user_id, purpose, model, item_count, prompt_tokens, completion_tokens, status,
                                            key_source, cost_usd)
                      VALUES (%s, 'categorize', 'm/a', 10, 100, 50, 'ok', 'shared', 0.01),
                             (%s, 'extract', 'm/a', 0, 10, 5, 'error', 'own', NULL)""", (uid, uid))
        db.execute("INSERT INTO email_log (user_id, to_address, template, category, status) VALUES (%s, 'a@b', 'welcome', 'auth', 'failed')", (uid,))

    def test_health(self):
        import admin_system
        with self.app.test_request_context():
            imports = admin_system.imports_health()
            errors = admin_system.errors_health()
            email = admin_system.email_health()
            storage = admin_system.storage_health()
            stripe = admin_system.stripe_health()
            backups = admin_system.backup_health()
        self.assertEqual([s["id"] for s in imports["stuck"]], [self.ids["stuck"]])
        self.assertEqual((imports["status"], imports["failed_24h"]), ("error", 1))
        self.assertEqual((errors["count_24h"], errors["groups"][0]["n"], errors["groups"][0]["message"]), (2, 2, "y"))
        self.assertEqual(email["failed_24h"], 1)
        self.assertGreater(storage["db_bytes"], 0)
        self.assertTrue(any(t["name"] == "statements" for t in storage["tables"]))
        self.assertEqual((stripe["enabled"], backups["status"]), (False, "warn"))

    def test_stopping_a_stuck_import(self):
        import admin_system
        with self.app.test_request_context():
            from flask import session
            session["user_id"] = self.uid
            res = admin_system.fail_stuck_import.__wrapped__(self.ids["fresh"])
        self.assertEqual(res[1], 409, "a two-minute-old import is still working")
        row = self.db.query("SELECT status FROM statements WHERE id = %s", (self.ids["fresh"],), one=True)
        self.assertEqual(row["status"], "parsing")

    def test_reports(self):
        import admin_range
        import admin_system
        rng = admin_range.parse({"range": "7d"})
        imp = admin_system.imports_report(rng)
        self.assertEqual((imp["totals"]["uploaded"], imp["totals"]["failed"]), (4, 1))
        self.assertEqual(imp["by_profile"][0]["profile"], "chase")
        self.assertEqual(imp["error_messages"], [{"message": "Could not read", "n": 1}])
        ai = admin_system.ai_report(rng)
        self.assertEqual({k: ai["totals"][k] for k in ("calls", "errors", "tokens", "cost_usd", "shared_cost_usd", "cost_is_partial")},
                         {"calls": 2, "errors": 1, "tokens": 165, "cost_usd": 0.01, "shared_cost_usd": 0.01, "cost_is_partial": True})
        self.assertEqual(sorted(r["key"] for r in ai["by_purpose"]), ["categorize", "extract"])


if __name__ == "__main__":
    unittest.main()
