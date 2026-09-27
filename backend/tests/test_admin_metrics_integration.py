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


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class RevenueTest(_pg.PgTestCase):
    """Five customers over the last 70 days, every kind of MRR movement in the last 30:

    amy  1000 since day -40, upgraded to 1500 on day -10          expansion   +500
    bob  1000 since day -50, cancelled on day -20                 churn      −1000
    cat   500 from day -60 to day -45, back at 500 on day -5      reactivation +500
    dan   800 from day -3                                          new         +800
    eve  2000 since day -70, downgraded to 1200 on day -15        contraction −800
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db = cls.db
        ids = {}
        for name in ("amy", "bob", "cat", "dan", "eve"):
            ids[name] = db.execute("INSERT INTO users (username, password_hash, created_at) VALUES (%s, 'x', %s) RETURNING id",
                                   (name, ago(80)), returning=True)["id"]
        moves = [("amy", 0, 1000, 40), ("amy", 1000, 1500, 10), ("bob", 0, 1000, 50), ("bob", 1000, 0, 20),
                 ("cat", 0, 500, 60), ("cat", 500, 0, 45), ("cat", 0, 500, 5), ("dan", 0, 800, 3),
                 ("eve", 0, 2000, 70), ("eve", 2000, 1200, 15)]
        for name, m0, m1, days in moves:
            db.execute("""INSERT INTO subscription_events (user_id, source, kind, mrr_from_cents, mrr_to_cents,
                                                           currency, occurred_at)
                          VALUES (%s, 'webhook', 'x', %s, %s, 'usd', %s)""", (ids[name], m0, m1, ago(days)))
        db.execute("INSERT INTO subscriptions (user_id, status, currency, mrr_cents) VALUES (%s, 'active', 'usd', 1500)",
                   (ids["amy"],))

    def test_the_bridge_and_its_ratios(self):
        import admin_metrics
        import admin_range
        rng = admin_range.parse({"range": "30d"})
        d = admin_metrics.revenue(rng)
        b = d["bridge_total"]
        self.assertEqual({k: b[k] for k in ("start_mrr", "end_mrr", "new_mrr", "reactivation_mrr", "expansion_mrr",
                                            "contraction_mrr", "churned_mrr", "customers_start", "customers_end")},
                         {"start_mrr": 4000, "end_mrr": 4000, "new_mrr": 800, "reactivation_mrr": 500,
                          "expansion_mrr": 500, "contraction_mrr": 800, "churned_mrr": 1000,
                          "customers_start": 3, "customers_end": 4})
        tiles = {t["key"]: t["value"] for t in d["tiles"]}
        self.assertEqual((tiles["net_new_mrr"], tiles["arr"], tiles["arpa"]), (0, 48000, 1000))
        self.assertAlmostEqual(tiles["customer_churn"], 1 / 3)
        self.assertAlmostEqual(tiles["revenue_churn"], 0.45)
        self.assertAlmostEqual(tiles["nrr"], 0.675)
        weekly = d["bridge"]
        self.assertEqual((sum(weekly["new"]), sum(weekly["churned"]), sum(weekly["reactivation"])), (800, 1000, 500),
                         "the weekly bars add up to the period")


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class FunnelTest(_pg.PgTestCase):
    """one   signed up 20 days ago, confirmed, uploaded and imported on day 2, paid on day 10
    two   signed up 15 days ago, confirmed, uploaded on day 1 but imported only on day 12 (too late)
    three signed up 10 days ago, never confirmed
    four  signed up 5 days ago, confirmed, never uploaded"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db = cls.db
        rows = [("one", 20, 19, 18, 18), ("two", 15, 14, 14, 3), ("three", 10, None, None, None), ("four", 5, 4, None, None)]
        cls.ids = {}
        for name, created, confirmed, upload, commit in rows:
            cls.ids[name] = db.execute(
                """INSERT INTO users (username, password_hash, created_at, email_verified_at, first_upload_at, first_commit_at)
                   VALUES (%s, 'x', %s, %s, %s, %s) RETURNING id""",
                (name, ago(created), confirmed and ago(confirmed), upload and ago(upload), commit and ago(commit)),
                returning=True)["id"]
        db.execute("""INSERT INTO payments (user_id, stripe_invoice_id, status, currency, amount_paid_cents, paid_at)
                      VALUES (%s, 'in_one', 'paid', 'usd', 999, %s)""", (cls.ids["one"], ago(10)))
        db.execute("""INSERT INTO subscription_events (user_id, source, kind, mrr_from_cents, mrr_to_cents, occurred_at,
                                                       stripe_subscription_id)
                      VALUES (%s, 'webhook', 'subscribed', 0, 999, %s, 'sub_one')""", (cls.ids["one"], ago(10)))
        import activity
        today = activity.today()
        db.execute("INSERT INTO user_activity_days (user_id, day, kinds) VALUES (%s, %s, 2), (%s, %s, 8)",
                   (cls.ids["one"], today - timedelta(days=18), cls.ids["one"], today - timedelta(days=4)))

    def _rng(self):
        import admin_range
        return admin_range.parse({"range": "30d"})

    def test_steps_require_the_one_before(self):
        import admin_metrics
        for confirm, expected in (("1", [4, 3, 2, 1, 1]), ("0", [4, 2, 1, 1])):
            with self.subTest(confirm=confirm):
                self.db.set_setting("signup_require_verification", confirm)
                d = admin_metrics.funnel(self._rng())
                self.assertEqual([s["n"] for s in d["steps"]], expected)
        self.assertEqual(d["steps"][0]["pct_prev"], None)
        self.assertEqual([(s["channel"], s["signups"], s["activated"]) for s in d["by_source"]], [("unknown", 4, 1)])

    def test_trial_cohorts_wait_for_the_window(self):
        import admin_metrics
        d = admin_metrics.trial_cohorts(self._rng())
        t = d["total"]
        self.assertEqual((t["signups"], t["added_card"], t["paid_in_window"], t["paid_ever"]), (4, 1, 1, 1))
        self.assertEqual(t["maturing"], 4, "nobody has had trial + 7 days yet")
        self.assertIsNone(t["rate"], "no settled cohort, no rate")

    def test_retention_cells_never_exceed_the_cohort(self):
        import admin_metrics
        r = admin_metrics.retention(self._rng(), "weekly")
        self.assertEqual(sum(row["size"] for row in r["rows"]), 4)
        for row in r["rows"]:
            for cell in row["cells"]:
                if cell:
                    self.assertLessEqual(cell["n"], row["size"])
        one_cohort = next(row for row in r["rows"] if row["cells"][0] and row["cells"][0]["n"])
        self.assertEqual(one_cohort["cells"][0]["n"], 1, "one imported in the week they signed up")
