"""Admin metrics against a real database, with every expected number worked out by hand.

    ISPEND_TEST_DSN=postgresql://... python -m unittest tests.test_admin_metrics_integration
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import _pg  # noqa: E402

NOW = datetime.now(timezone.utc)


def ago(days, hours=0):
    return NOW - timedelta(days=days, hours=hours)


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class OverviewTest(_pg.PgTestCase):
    """Five people, a month of history:

    ann    signed up 20 days ago, imported 2 days later, subscribed 10 days ago at 10.00/month,
           read a report 2 days ago
    bob    signed up 15 days ago, only ever opened the app (yesterday)
    cat    signed up 3 days ago (her first week is not over)
    dan    signed up 70 days ago, subscribed yearly (99.99 = 8.33/month) 50 days ago, cancelled 5 days ago
    root   an administrator, signed up yesterday (never counted)
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db = cls.db

        def user(name, created, role="user", first_commit=None):
            return db.execute(
                """INSERT INTO users (username, password_hash, role, created_at, first_commit_at)
                   VALUES (%s, 'x', %s, %s, %s) RETURNING id""", (name, role, created, first_commit),
                returning=True)["id"]

        cls.ann = user("ann", ago(20), first_commit=ago(18))
        cls.bob = user("bob", ago(15))
        cls.cat = user("cat", ago(3))
        cls.dan = user("dan", ago(70))
        user("root", ago(1), role="admin")

        def event(uid, kind, m0, m1, at, currency="usd"):
            db.execute("""INSERT INTO subscription_events (user_id, source, kind, mrr_from_cents, mrr_to_cents,
                                                           currency, occurred_at)
                          VALUES (%s, 'webhook', %s, %s, %s, %s, %s)""", (uid, kind, m0, m1, currency, at))

        event(cls.ann, "trial_started", 0, 0, ago(20))
        event(cls.ann, "subscribed", 0, 1000, ago(10))
        event(cls.dan, "subscribed", 0, 833, ago(50))
        event(cls.dan, "canceled", 833, 0, ago(5))
        for uid, state in ((cls.ann, "active"), (cls.bob, "trialing"), (cls.cat, "trialing"), (cls.dan, "read_only")):
            db.execute("""INSERT INTO subscriptions (user_id, status, ent_state, trial_end, currency)
                          VALUES (%s, 'trialing', %s, %s, 'usd')""",
                       (uid, state, ago(-3) if uid == cls.cat else ago(-10)))

        import activity
        today = activity.today()
        for uid, days_ago, bits in ((cls.ann, 18, activity.IMPORT), (cls.ann, 2, activity.REPORT),
                                    (cls.bob, 1, activity.SEEN)):
            db.execute("INSERT INTO user_activity_days (user_id, day, kinds) VALUES (%s, %s, %s)",
                       (uid, today - timedelta(days=days_ago), bits))
        db.execute("""INSERT INTO payments (user_id, stripe_invoice_id, status, currency, failed_at)
                      VALUES (%s, 'in_f', 'failed', 'usd', %s)""", (cls.ann, ago(4)))

    def _overview(self, **args):
        import admin_metrics
        import admin_range
        return admin_metrics.overview(admin_range.parse({"range": "30d", **args}))

    def test_tiles(self):
        tiles = {t["key"]: (t["value"], t["prev"]) for t in self._overview()["tiles"]}
        self.assertEqual(tiles, {
            "mrr": (1000, 833),                # ann now; dan a month ago
            "paying": (1, 1),
            "trialing": (2, None),             # bob and cat
            "trials_ending": (1, None),        # cat's ends in 3 days
            "signups": (3, 0),                 # ann, bob, cat; root is an admin, dan is older
            "activation": (0.5, None),         # of ann and bob (cat is too recent), ann imported in week one
            "importers": (1, 0),
            "wau": (1, 0),                     # ann read a report; bob only opened the app
            "churned": (1, 0),                 # dan
            "failed_payments": (1, 0),
        })

    def test_series_line_up_with_the_buckets(self):
        s = self._overview(range="7d")["series"]
        self.assertEqual(len(s["labels"]), 7)
        self.assertEqual({k: len(v) for k, v in s.items() if k != "labels"},
                         {"signups": 7, "activated": 7, "wau": 7, "mau": 7, "mrr": 7})
        self.assertEqual((s["signups"][-4], s["mrr"][-1], s["mrr"][0]), (1, 1000, 1833),
                         "cat signed up 3 days ago; dan's 8.33 was still counted a week ago")

    def test_comparison_series_when_asked(self):
        prev = self._overview(range="7d", compare="1")["series"]["prev"]
        self.assertEqual({k: len(v) for k, v in prev.items()}, {"signups": 7, "wau": 7, "mrr": 7})

    def test_alerts_include_a_missing_backup(self):
        kinds = [a["kind"] for a in self._overview()["alerts"]]
        self.assertIn("backup_stale", kinds)

    def test_snapshot_is_idempotent(self):
        import admin_metrics
        import activity
        day = activity.today()
        admin_metrics.snapshot(day)
        admin_metrics.snapshot(day)
        rows = self.db.query("SELECT metric, dim, value FROM metric_daily WHERE day = %s ORDER BY 1, 2", (day,))
        self.assertEqual([(r["metric"], r["dim"], int(r["value"])) for r in rows], [
            ("ent:active", "", 1), ("ent:grace", "", 0), ("ent:read_only", "", 1), ("ent:trialing", "", 2),
            ("mrr_cents", "usd", 0), ("paying", "usd", 0),
        ])

    def test_cache_round_trip(self):
        import admin_metrics
        first = admin_metrics.cached("it", lambda: {"n": 1})
        second = admin_metrics.cached("it", lambda: {"n": 2})
        self.assertEqual((first["n"], second["n"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
