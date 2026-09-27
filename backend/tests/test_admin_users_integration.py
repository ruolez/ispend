"""The people list and the one-person view against a real database.

    ISPEND_TEST_DSN=postgresql://... python -m unittest tests.test_admin_users_integration
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import _pg  # noqa: E402

NOW = datetime.now(timezone.utc)


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class PeopleTest(_pg.PgTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db = cls.db

        def user(name, days_ago, status="active", first_commit=None, email=None):
            return db.execute(
                """INSERT INTO users (username, email, password_hash, created_at, status, first_commit_at,
                                      deleted_at)
                   VALUES (%s, %s, 'x', %s, %s, %s, %s) RETURNING id""",
                (name, email, NOW - timedelta(days=days_ago), status, first_commit,
                 NOW if status == "deleted" else None), returning=True)["id"]

        cls.root = db.query("SELECT id FROM users WHERE username = 'admin'", one=True)["id"]   # seeded by migrations
        cls.amy = user("amy", 30, first_commit=NOW - timedelta(days=29), email="amy@example.com")
        cls.bob = user("bob", 10, email="bob@example.com")
        cls.gone = user("gone", 50, status="deleted")
        db.execute("INSERT INTO subscriptions (user_id, status, ent_state, plan, mrr_cents) VALUES (%s, 'active', 'active', 'monthly', 999)",
                   (cls.amy,))
        db.execute("INSERT INTO subscriptions (user_id, status, ent_state) VALUES (%s, 'trialing', 'trialing')", (cls.bob,))
        db.execute("INSERT INTO signup_attribution (user_id, channel) VALUES (%s, 'search')", (cls.amy,))
        cls.vip = db.execute("INSERT INTO admin_tags (name, color) VALUES ('VIP', 'c3') RETURNING id", returning=True)["id"]
        db.execute("INSERT INTO user_admin_tags (user_id, tag_id) VALUES (%s, %s)", (cls.amy, cls.vip))
        db.execute("""INSERT INTO payments (user_id, stripe_invoice_id, status, currency, amount_paid_cents,
                                            amount_refunded_cents, paid_at)
                      VALUES (%s, 'in_1', 'paid', 'usd', 999, 0, now() - interval '5 days'),
                             (%s, 'in_2', 'partially_refunded', 'usd', 999, 500, now() - interval '2 days')""",
                   (cls.amy, cls.amy))
        db.execute("INSERT INTO subscription_events (user_id, source, kind, mrr_from_cents, mrr_to_cents, occurred_at) "
                   "VALUES (%s, 'webhook', 'subscribed', 0, 999, now() - interval '6 days')", (cls.amy,))
        db.execute("INSERT INTO admin_notes (user_id, body, pinned) VALUES (%s, 'Asked about CSV', true)", (cls.amy,))

    def _ids(self, **args):
        import admin_users
        return admin_users.matching_ids(args, 50)

    def test_filters(self):
        cases = [
            ({}, {self.root, self.amy, self.bob}),
            ({"status": "all"}, {self.root, self.amy, self.bob, self.gone}),
            ({"state": "trialing"}, {self.bob}),
            ({"plan": "monthly"}, {self.amy}),
            ({"activated": "1"}, {self.amy}),
            ({"activated": "0"}, {self.root, self.bob}),
            ({"source": "search"}, {self.amy}),
            ({"source": "unknown"}, {self.root, self.bob}),
            ({"tag": str(self.vip)}, {self.amy}),
            ({"q": "bob@"}, {self.bob}),
            ({"q": f"#{self.bob}"}, {self.bob}),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                ids, total = self._ids(**args)
                self.assertEqual((set(ids), total), (expected, len(expected)))

    def test_every_sort_runs(self):
        import admin_users
        for key in admin_users.SORTS:
            for direction in ("asc", "desc"):
                with self.subTest(sort=key, dir=direction):
                    ids, _ = self._ids(sort=key, dir=direction)
                    self.assertEqual(set(ids), {self.root, self.amy, self.bob})
        self.assertEqual(self._ids(sort="lifetime", dir="desc")[0][0], self.amy)

    def test_rows_carry_money_tags_and_activation(self):
        import admin_users
        with self.app.test_request_context():
            rows = admin_users.user_rows([self.bob, self.amy])
        amy = rows[1]
        self.assertEqual([r["id"] for r in rows], [self.bob, self.amy])
        self.assertEqual((amy["lifetime_cents"], amy["tags"], amy["activated"], amy["source"], amy["state"]),
                         (1498, [{"id": self.vip, "name": "VIP", "color": "c3"}], True, "search", "active"))

    def test_person_extras(self):
        import admin_users
        with self.app.test_request_context():
            extras = admin_users.person_extras(admin_users._load(self.amy))
        self.assertEqual(extras["subscription"]["lifetime_cents"], 1498)
        self.assertEqual([n["body"] for n in extras["notes"]], ["Asked about CSV"])
        kinds = [t["kind"] for t in extras["timeline"]]
        self.assertEqual(kinds[:3], ["payment", "payment", "subscribed"], "newest first")
        self.assertEqual(kinds[-2:], ["import", "signup"])
        self.assertTrue(extras["activation"]["activated"])


if __name__ == "__main__":
    unittest.main()
