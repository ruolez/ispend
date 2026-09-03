import json
import os
import sys
import types
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")


@contextmanager
def _tx():
    yield None


sys.modules.setdefault("db", types.SimpleNamespace(
    query=lambda *a, **k: None, execute=lambda *a, **k: 0, execute_values=lambda *a, **k: None, transaction=_tx,
    get_setting=lambda *a, **k: None, set_setting=lambda *a, **k: None, get_db=lambda: None,
    close_db=lambda *a, **k: None))
DB = sys.modules["db"]

import insights  # noqa: E402
import openrouter  # noqa: E402
import reports  # noqa: E402

START, END = date(2026, 8, 1), date(2026, 8, 31)
SUMMARY = {"income": 5000.0, "expenses": 3200.5, "net": 1799.5, "txn_count": 40, "uncategorized": 2}


def fake_reports():
    return [
        mock.patch.object(reports, "summary", return_value=SUMMARY),
        mock.patch.object(reports, "by_category", return_value=[{"name": "Dining", "total": 400.0, "pct": 12.5, "count": 9}]),
        mock.patch.object(reports, "month_over_month", return_value={"categories": [
            {"name": "Dining", "current": 400.0, "previous": 300.0, "delta": 100.0, "pct": 33.3}]}),
        mock.patch.object(reports, "top_merchants", return_value=[{"merchant_name": "Costco", "total": 250.0, "count": 2}]),
        mock.patch.object(reports, "recurring", return_value=[{"merchant_name": "Netflix", "cadence": "monthly",
                                                                "median_amount": 15.99, "monthly_equivalent": 15.99,
                                                                "next_expected": "2026-09-14", "is_active": True}]),
        mock.patch.object(reports, "anomalies", return_value=[]),
        mock.patch.object(reports, "largest", return_value=[{"merchant_name": "Costco", "amount": -180.0,
                                                              "txn_date": "2026-08-03", "category_name": "Groceries"}]),
    ]


class InsightsTest(unittest.TestCase):
    def setUp(self):
        self.p = fake_reports()
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_build_context_shape(self):
        ctx = insights.build_context(1, START, END)
        self.assertEqual(ctx["period"], {"start": "2026-08-01", "end": "2026-08-31", "days": 31})
        self.assertEqual(ctx["totals"]["expenses"], 3200.5)
        self.assertEqual(ctx["recurring"][0]["name"], "Netflix")
        self.assertEqual(ctx["largest_purchases"][0]["amount"], 180.0)

    def test_generate_validates_and_stores(self):
        reply = {"summary": "You spent less.", "insights": [
            {"title": "Dining up", "body": "Dining rose by 100.00.", "severity": "warn", "category": "Dining"},
            {"body": "no title -> dropped"},
            {"title": "Weird severity", "body": "x", "severity": "purple"},
        ]}
        stored = {}

        def fake_execute(sql, params=None, returning=False, **k):
            stored["params"] = params
            return {"content": params[4], "model": params[3], "created_at": datetime(2026, 9, 3, 12, 0)}

        with mock.patch.object(DB, "query", return_value=None, create=True), \
             mock.patch.object(DB, "execute", side_effect=fake_execute, create=True), \
             mock.patch.object(openrouter, "enabled", return_value=True), \
             mock.patch.object(openrouter, "model", return_value="test/model"), \
             mock.patch.object(openrouter, "log_call"), \
             mock.patch.object(openrouter, "chat_json", return_value=(reply, {})):
            out = insights.generate(1, START, END)
        self.assertEqual(out["cached"], False)
        self.assertEqual(out["model"], "test/model")
        self.assertEqual(out["content"]["summary"], "You spent less.")
        self.assertEqual([(i["title"], i["severity"]) for i in out["content"]["insights"]],
                         [("Dining up", "warn"), ("Weird severity", "info")])
        self.assertEqual(json.loads(stored["params"][4])["summary"], "You spent less.")

    def test_generate_returns_cache_without_calling_model(self):
        row = {"content": json.dumps({"summary": "cached", "insights": []}), "model": "m", "created_at": datetime(2026, 9, 1)}
        with mock.patch.object(DB, "query", return_value=row, create=True), mock.patch.object(openrouter, "chat_json") as chat:
            out = insights.generate(1, START, END)
        chat.assert_not_called()
        self.assertEqual((out["cached"], out["content"]["summary"]), (True, "cached"))

    def test_generate_disabled_raises(self):
        with mock.patch.object(DB, "query", return_value=None, create=True), mock.patch.object(openrouter, "enabled", return_value=False):
            with self.assertRaises(openrouter.OpenRouterError):
                insights.generate(1, START, END)


if __name__ == "__main__":
    unittest.main()
