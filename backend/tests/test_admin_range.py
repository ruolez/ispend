import os
import sys
import unittest
from datetime import date, datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

import admin_metrics  # noqa: E402
import admin_range  # noqa: E402

TODAY = date(2026, 3, 10)


def parse(**args):
    return admin_range.parse(args, now_day=TODAY, tz="America/Chicago")


class ParseTest(unittest.TestCase):
    def test_presets(self):
        cases = [
            # (args, start, end (exclusive), bucket)
            ({}, date(2026, 2, 9), date(2026, 3, 11), "day"),
            ({"range": "7d"}, date(2026, 3, 4), date(2026, 3, 11), "day"),
            ({"range": "90d"}, date(2025, 12, 11), date(2026, 3, 11), "week"),
            ({"range": "12m"}, date(2025, 3, 11), date(2026, 3, 11), "month"),
            ({"range": "mtd"}, date(2026, 3, 1), date(2026, 3, 11), "day"),
            ({"range": "ytd"}, date(2026, 1, 1), date(2026, 3, 11), "week"),
            ({"range": "custom", "from": "2024-01-01", "to": "2025-12-31"}, date(2024, 1, 1), date(2026, 1, 1), "month"),
        ]
        for args, start, end, bucket in cases:
            with self.subTest(args=args):
                r = parse(**args)
                self.assertEqual((r["start"], r["end"], r["bucket"]), (start, end, bucket))
                self.assertEqual((r["prev_end"], r["end"] - r["start"]), (start, r["start"] - r["prev_start"]),
                                 "the comparison period is the same length, immediately before")

    def test_bad_ranges_are_user_errors(self):
        cases = [({"range": "forever"}, "range must be one of"),
                 ({"range": "custom", "from": "2026-03-01", "to": "2026-02-01"}, "on or after"),
                 ({"range": "custom", "from": "2020-01-01", "to": "2026-01-01"}, "three years"),
                 ({"range": "custom", "from": "yesterday", "to": "2026-01-01"}, "from must be a date")]
        for args, message in cases:
            with self.subTest(args=args), self.assertRaises(admin_range.RangeError) as ctx:
                parse(**args)
            self.assertIn(message, str(ctx.exception))

    def test_compare_flag(self):
        self.assertEqual([parse(compare=v)["compare"] for v in ("1", "prev", "0", None)], [True, True, False, False])

    def test_day_bounds_are_local_midnight(self):
        """A day in America/Chicago starts at 05:00 or 06:00 UTC depending on daylight saving."""
        winter = admin_range.at(date(2026, 3, 1), "America/Chicago").astimezone(timezone.utc)
        summer = admin_range.at(date(2026, 3, 9), "America/Chicago").astimezone(timezone.utc)
        self.assertEqual((winter.hour, summer.hour), (6, 5))


class BucketTest(unittest.TestCase):
    def test_buckets_align_to_calendar_edges_and_clip_to_the_range(self):
        cases = [
            (date(2026, 3, 1), date(2026, 3, 4), "day",
             [(date(2026, 3, 1), date(2026, 3, 2)), (date(2026, 3, 2), date(2026, 3, 3)), (date(2026, 3, 3), date(2026, 3, 4))]),
            # 2026-03-04 is a Wednesday: the first week is clipped, then Monday to Monday.
            (date(2026, 3, 4), date(2026, 3, 20), "week",
             [(date(2026, 3, 4), date(2026, 3, 9)), (date(2026, 3, 9), date(2026, 3, 16)), (date(2026, 3, 16), date(2026, 3, 20))]),
            (date(2025, 12, 15), date(2026, 2, 10), "month",
             [(date(2025, 12, 15), date(2026, 1, 1)), (date(2026, 1, 1), date(2026, 2, 1)), (date(2026, 2, 1), date(2026, 2, 10))]),
        ]
        for start, end, bucket, expected in cases:
            with self.subTest(bucket=bucket):
                self.assertEqual(admin_range.buckets(start, end, bucket), expected)

    def test_buckets_cover_every_day_exactly_once(self):
        for bucket in ("day", "week", "month"):
            with self.subTest(bucket=bucket):
                b = admin_range.buckets(date(2025, 1, 7), date(2026, 5, 3), bucket)
                self.assertEqual((b[0][0], b[-1][1]), (date(2025, 1, 7), date(2026, 5, 3)))
                self.assertTrue(all(prev[1] == nxt[0] for prev, nxt in zip(b, b[1:], strict=False)))

    def test_per_bucket_sums_days(self):
        daily = {date(2026, 3, 4): 2, date(2026, 3, 8): 1, date(2026, 3, 9): 5, date(2026, 4, 1): 9}
        bkts = admin_range.buckets(date(2026, 3, 4), date(2026, 3, 20), "week")
        self.assertEqual(admin_metrics.per_bucket(daily, bkts), [3, 5, 0])


class TileTest(unittest.TestCase):
    def test_deltas(self):
        cases = [((120, 100), (20, 0.2)), ((80, 100), (-20, -0.2)), ((5, 0), (5, None)),
                 ((5, None), (None, None)), ((None, 3), (None, None))]
        for (value, prev), expected in cases:
            with self.subTest(value=value, prev=prev):
                self.assertEqual(admin_metrics.delta(value, prev), expected)

    def test_cache_hit_and_miss(self):
        computed_at = datetime(2026, 3, 10, 12, tzinfo=timezone.utc)
        q = _stubs.Router([("FROM metric_cache", {"payload": {"n": 1}, "computed_at": computed_at})])
        x = _stubs.Router(default=1)
        with mock.patch.object(FAKE, "query", side_effect=q), mock.patch.object(FAKE, "execute", side_effect=x):
            hit = admin_metrics.cached("k", lambda: {"n": 2})
            miss = admin_metrics.cached("k", lambda: {"n": 3, "d": date(2026, 1, 2)}, refresh=True)
        self.assertEqual(hit, {"n": 1, "as_of": computed_at.isoformat()})
        self.assertEqual({k: miss[k] for k in ("n", "d")}, {"n": 3, "d": "2026-01-02"})
        self.assertEqual(len(x.sql("INSERT INTO metric_cache")), 1)


class RouteTest(unittest.TestCase):
    def test_overview_is_admin_only_and_validates_the_range(self):
        from flask import Flask
        app = Flask(__name__)
        app.secret_key = "t"
        app.register_blueprint(admin_metrics.bp)
        cases = [(None, None, "", 401), (2, "user", "", 403), (1, "admin", "?range=forever", 400)]
        for uid, role, qs, status in cases:
            with self.subTest(role=role):
                c = app.test_client()
                if uid:
                    with c.session_transaction() as sess:
                        sess["user_id"], sess["role"] = uid, role
                self.assertEqual(c.get(f"/api/admin/metrics/overview{qs}").status_code, status)


if __name__ == "__main__":
    unittest.main()
