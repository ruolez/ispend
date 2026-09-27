"""Engagement and operations capture: activity days, last seen, attribution, the email log, the
error log and AI cost."""
import os
import sys
import types
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import activity  # noqa: E402
import attribution  # noqa: E402
import auth  # noqa: E402
import errors  # noqa: E402
import mailer  # noqa: E402
import openrouter  # noqa: E402
import ops_tick  # noqa: E402

DAY = date(2026, 9, 15)


class _Db(unittest.TestCase):
    def setUp(self):
        FAKE.settings.clear()
        activity._memo.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)
        self.patches = [mock.patch.object(FAKE, "query", side_effect=self.q),
                        mock.patch.object(FAKE, "execute", side_effect=self.x),
                        mock.patch.object(activity, "today", return_value=DAY)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()


class ActivityTest(_Db):
    def test_routes_map_to_kinds(self):
        cases = [
            ("POST", "/api/statements/<int:statement_id>/commit", activity.IMPORT),
            ("POST", "/api/statements", activity.UPLOAD),
            ("PUT", "/api/transactions/<int:txn_id>", activity.CATEGORIZE),
            ("GET", "/api/reports/summary", activity.REPORT),
            ("GET", "/api/reports/dashboard", activity.DASHBOARD),
            ("GET", "/api/transactions", 0),
            ("DELETE", "/api/statements/<int:statement_id>", 0),
        ]
        for method, rule, bit in cases:
            with self.subTest(rule=rule):
                self.assertEqual(activity.bits_for(method, rule), bit)
        self.assertEqual(activity.VALUE_MASK & (activity.SEEN | activity.DASHBOARD | activity.UPLOAD), 0,
                         "opening the app is not the same as using it")

    def test_one_write_per_person_kind_and_day(self):
        self.assertEqual([activity.touch(5, activity.REPORT), activity.touch(5, activity.REPORT),
                          activity.touch(5, activity.REPORT | activity.CATEGORIZE), activity.touch(6, activity.REPORT)],
                         [activity.REPORT, 0, activity.CATEGORIZE, activity.REPORT])
        self.assertEqual([p for _, p in self.x.sql("INSERT INTO user_activity_days")],
                         [(5, DAY, activity.REPORT), (5, DAY, activity.CATEGORIZE), (6, DAY, activity.REPORT)])
        self.assertIn("kinds = user_activity_days.kinds | EXCLUDED.kinds", self.x.sql("INSERT INTO user_activity_days")[0][0])

    def test_the_first_import_and_upload_are_stamped_for_good(self):
        activity.touch(5, activity.IMPORT)
        activity.touch(5, activity.UPLOAD)
        stamps = [s for s, _ in self.x.sql("UPDATE users")]
        self.assertEqual(len(stamps), 2)
        self.assertIn("first_commit_at = COALESCE(first_commit_at, now())", stamps[0])
        self.assertIn("first_upload_at = COALESCE(first_upload_at, now())", stamps[1])

    def test_only_successful_signed_in_requests_count(self):
        rule = types.SimpleNamespace(rule="/api/reports/summary")
        cases = [(200, 5, rule, 1), (404, 5, rule, 0), (200, None, rule, 0), (200, 5, None, 0)]
        for status, uid, url_rule, writes in cases:
            with self.subTest(status=status, uid=uid):
                activity._memo.clear()
                self.x.calls.clear()
                req = types.SimpleNamespace(method="GET", url_rule=url_rule)
                activity.from_request(req, types.SimpleNamespace(status_code=status), uid)
                self.assertEqual(len(self.x.sql("INSERT INTO user_activity_days")), writes)

    def test_bookkeeping_never_breaks_the_response(self):
        self.x.routes.insert(0, ("INSERT INTO user_activity_days", lambda s, p: (_ for _ in ()).throw(RuntimeError())))
        req = types.SimpleNamespace(method="GET", url_rule=types.SimpleNamespace(rule="/api/reports/summary"))
        with self.assertLogs("activity", "WARNING"):
            activity.from_request(req, types.SimpleNamespace(status_code=200), 5)


class LastSeenTest(_Db):
    def test_written_at_most_every_five_minutes(self):
        now = datetime.now(timezone.utc)
        cases = [(None, True), (now - timedelta(minutes=6), True), (now - timedelta(minutes=2), False)]
        for seen, writes in cases:
            with self.subTest(seen=seen):
                activity._memo.clear()
                self.x.calls.clear()
                auth._mark_seen({"id": 5, "last_seen_at": seen})
                self.assertEqual(len(self.x.sql("SET last_seen_at = now()")), 1 if writes else 0)
                self.assertEqual(len(self.x.sql("INSERT INTO user_activity_days")), 1 if writes else 0)


class AttributionTest(unittest.TestCase):
    def test_channels(self):
        cases = [
            ({"utm_source": "Newsletter", "utm_medium": "email"}, "newsletter"),
            ({"referrer_host": "www.google.com"}, "search"),
            ({"referrer_host": "duckduckgo.com"}, "search"),
            ({"referrer_host": "old.reddit.com"}, "social"),
            ({"referrer_host": "t.co"}, "social"),
            ({"referrer_host": "someblog.example.org"}, "someblog.example.org"),
            ({"referrer_host": "ispend.app"}, "direct"),
            ({"referrer_host": "app.ispend.app"}, "direct"),
            ({"utm_medium": "cpc"}, "cpc"),
            ({}, "direct"),
        ]
        for payload, expected in cases:
            with self.subTest(payload=payload):
                self.assertEqual(attribution.clean(payload, own_host="ispend.app")["channel"], expected)

    def test_values_are_bounded_and_only_the_site_is_kept(self):
        row = attribution.clean({"utm_campaign": "Spring <b>" + "x" * 300, "referrer_host": "https://news.example.com/a/b?q=1",
                                 "landing_path": "/pricing?secret=1", "first_seen_at": "not a date",
                                 "utm_term": 42}, own_host="ispend.app")
        self.assertEqual({k: row[k] for k in ("utm_campaign", "referrer_host", "landing_path", "first_seen_at", "utm_term")},
                         {"utm_campaign": "spring b" + "x" * 90, "referrer_host": "news.example.com",
                          "landing_path": "/pricing", "first_seen_at": None, "utm_term": None})
        self.assertIsNone(attribution.clean("junk"))


class EmailLogTest(_Db):
    def test_every_send_is_logged_with_its_outcome(self):
        cases = [((True, None, "<m@x>"), "sent"), ((False, "not configured", None), "skipped"),
                 ((False, "550 no", None), "failed")]
        for outcome, status in cases:
            with self.subTest(status=status):
                self.x = _stubs.Router([("INSERT INTO email_log", {"id": 9})], default=1)
                with mock.patch.object(FAKE, "execute", side_effect=self.x), \
                        mock.patch.object(mailer, "_deliver", return_value=outcome):
                    mailer.send_template("trial_ending", "a@b.co", user_id=5, username="amy")
                (_, queued), = self.x.sql("INSERT INTO email_log")
                self.assertEqual(queued, (5, "a@b.co", "trial_ending", "lifecycle", None))
                (_, result), = self.x.sql("UPDATE email_log")
                self.assertEqual((result[0], result[3], result[5]), (status, outcome[2], 9))

    def test_the_row_is_written_before_the_background_send(self):
        """So a message lost with its worker still shows up, as queued."""
        self.x = _stubs.Router([("INSERT INTO email_log", {"id": 3})], default=1)
        with mock.patch.object(FAKE, "execute", side_effect=self.x), mock.patch("jobs.spawn") as spawn:
            mailer.send_async("welcome", "a@b.co", user_id=5, username="amy")
        self.assertEqual(len(self.x.sql("INSERT INTO email_log")), 1)
        self.assertEqual(spawn.call_args.args[1:], ("welcome", "a@b.co", {"username": "amy"}, 3))

    def test_a_job_context_can_log(self):
        """The old audit-based record needed a request; background sends were never counted."""
        self.x.routes.append(("INSERT INTO email_log", {"id": 4}))
        with Flask(__name__).app_context(), mock.patch.object(mailer, "_deliver", return_value=(True, None, "m")):
            self.assertEqual(mailer.send_template("welcome", "a@b.co", username="amy"), (True, None))
        self.assertEqual(len(self.x.sql("UPDATE email_log")), 1)


class ErrorLogTest(unittest.TestCase):
    def setUp(self):
        errors._last.clear()

    def _raise(self, message="boom"):
        try:
            raise ValueError(message)
        except ValueError as e:
            return e

    def test_recorded_on_a_separate_connection_and_rate_limited(self):
        conn = mock.MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        with mock.patch.object(FAKE, "connect", return_value=conn, create=True):
            first = errors.record("job", self._raise("x" * 900), location="parse_statement")
            again = errors.record("job", self._raise("second"), location="parse_statement")
        self.assertEqual((first, again), (True, False))
        self.assertTrue(conn.autocommit)
        params = cursor.execute.call_args.args[1]
        self.assertEqual((params[0], params[2], len(params[3]), params[4]), ("job", "ValueError", 500, "parse_statement"))
        conn.close.assert_called_once()

    def test_same_bug_same_fingerprint(self):
        fps = {errors.fingerprint(self._raise(m))[0] for m in ("a", "b")}
        self.assertEqual(len(fps), 1)

    def test_never_raises(self):
        with mock.patch.object(FAKE, "connect", side_effect=RuntimeError("db down"), create=True), \
                self.assertLogs("errors", "WARNING"):
            self.assertFalse(errors.record("request", self._raise()))


class AiCostTest(unittest.TestCase):
    def setUp(self):
        FAKE.settings.clear()

    def test_whose_key_paid(self):
        FAKE.settings["u5:openrouter_api_key"] = "sk-own"
        self.assertEqual([openrouter.key_source(5), openrouter.key_source(6), openrouter.key_source(None)],
                         ["own", "shared", "shared"])

    def test_cost_is_reported_then_estimated_then_unknown(self):
        catalogue = [{"id": "m/a", "prompt_price": 1.0, "completion_price": 4.0}]
        usage = {"prompt_tokens": 1000, "completion_tokens": 500}
        with mock.patch.dict(openrouter._models_cache, {"data": catalogue}):
            self.assertEqual([openrouter._cost("m/a", {**usage, "cost": 0.0123}), openrouter._cost("m/a", usage),
                              openrouter._cost("m/unknown", usage), openrouter._cost("m/a", None)],
                             [0.0123, 0.003, None, 0.0])


class PruneTest(_Db):
    def test_operational_history_is_bounded(self):
        ops_tick.prune()
        self.assertEqual([(s.split()[2], p) for s, p in self.x.sql("DELETE FROM")],
                         [("app_errors", (30,)), ("login_events", (365,)), ("email_log", (365,))])


if __name__ == "__main__":
    unittest.main()
