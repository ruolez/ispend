import json
import os
import sys
import types
import unittest
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
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

from flask import Flask, session  # noqa: E402

import reports  # noqa: E402
import reports_api  # noqa: E402

NOW = date(2026, 9, 3)


class ResolveRangeTest(unittest.TestCase):
    def test_presets(self):
        cases = {
            "this-month": (date(2026, 9, 1), date(2026, 9, 30), date(2026, 8, 1), date(2026, 8, 31)),
            "last-month": (date(2026, 8, 1), date(2026, 8, 31), date(2026, 7, 1), date(2026, 7, 31)),
            "last-30": (date(2026, 8, 5), date(2026, 9, 3), date(2026, 7, 6), date(2026, 8, 4)),
            "this-year": (date(2026, 1, 1), date(2026, 12, 31), date(2025, 1, 1), date(2025, 12, 31)),
            "last-year": (date(2025, 1, 1), date(2025, 12, 31), date(2024, 1, 1), date(2024, 12, 31)),
        }
        for name, expected in cases.items():
            r = reports.resolve_range(name, now=NOW)
            self.assertEqual((r["start"], r["end"], r["prev_start"], r["prev_end"]), expected, name)

    def test_week_presets_follow_the_week_start_preference(self):
        # NOW (2026-09-03) is a Thursday
        r = reports.resolve_range("this-week", now=NOW)
        self.assertEqual((r["start"], r["end"], r["prev_start"], r["prev_end"]), (date(2026, 8, 30), date(2026, 9, 5), date(2026, 8, 23), date(2026, 8, 29)))
        r = reports.resolve_range("this-week", now=NOW, week_start=1)
        self.assertEqual((r["start"], r["end"]), (date(2026, 8, 31), date(2026, 9, 6)))
        r = reports.resolve_range("last-week", now=NOW, week_start=1)
        self.assertEqual((r["start"], r["end"], r["prev_start"], r["prev_end"]), (date(2026, 8, 24), date(2026, 8, 30), date(2026, 8, 17), date(2026, 8, 23)))
        self.assertEqual(reports.week_bounds(date(2026, 1, 1), 0), (date(2025, 12, 28), date(2026, 1, 3)))  # across a year boundary
        self.assertEqual(reports.week_bounds(date(2026, 9, 6), 0), (date(2026, 9, 6), date(2026, 9, 12)))  # a Sunday starts its own week

    def test_custom_and_all(self):
        r = reports.resolve_range(None, "2026-03-01", "2026-03-10", now=NOW)
        self.assertEqual((r["name"], r["start"], r["end"], r["prev_start"], r["prev_end"]),
                         ("custom", date(2026, 3, 1), date(2026, 3, 10), date(2026, 2, 19), date(2026, 2, 28)))
        r = reports.resolve_range("all", now=NOW)
        self.assertEqual((r["prev_start"], r["end"]), (None, reports.ALL_END))

    def test_month_helpers(self):
        self.assertEqual(reports.parse_month("2026-02", fallback=NOW), (2026, 2))
        self.assertEqual(reports.parse_month("garbage", fallback=NOW), (2026, 9))
        self.assertEqual(reports.shift_month(2026, 1, -1), (2025, 12))
        self.assertEqual(reports.month_bounds(2024, 2), (date(2024, 2, 1), date(2024, 2, 29)))


CAT_ROWS = [
    {"id": 4, "name": "Dining", "color": "c2", "icon": "utensils", "parent_id": None, "total": Decimal("300.00"), "count": 12},
    {"id": None, "name": None, "color": None, "icon": None, "parent_id": None, "total": Decimal("100.00"), "count": 3},
]


class DashboardSetupTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(reports_api.bp)

    def test_dashboard_carries_a_setup_block(self):
        base = {"range": {}, "kpis": {"txn_count": 3}, "accounts": [{"id": 1}], "review_count": {"uncategorized": 2, "suggested": 1}}
        counts = {"statements": 1, "rules": 0, "transactions": 3}
        with mock.patch.object(reports, "dashboard", return_value=dict(base)), \
                mock.patch.object(DB, "query", return_value=counts, create=True) as q, \
                mock.patch.object(reports_api.openrouter, "configured", return_value=True):
            with self.app.test_request_context("/api/reports/dashboard"):
                session["user_id"] = 1
                data = json.loads(reports_api.dashboard().get_data())
        self.assertEqual(data["setup"], {"accounts": 1, "statements": 1, "transactions": 3, "needs_review": 3, "rules": 0, "ai_configured": True})
        self.assertEqual(q.call_args.args[1], (1, 1, 1))


class ByCategoryEndpointTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(reports_api.bp)

    def _get(self, path):
        with self.app.test_request_context(path):
            session["user_id"] = 1
            return reports_api.by_category()

    def test_json_shape_with_uncategorized_and_pct(self):
        with mock.patch.object(DB, "query", return_value=CAT_ROWS, create=True) as q:
            data = json.loads(self._get("/api/reports/by-category?range=last-month").get_data())
        self.assertEqual(data["total"], 400.0)
        self.assertEqual(data["level"], "top")
        self.assertEqual(data["categories"], [
            {"id": 4, "name": "Dining", "color": "c2", "icon": "utensils", "parent_id": None, "total": 300.0, "count": 12, "pct": 75.0},
            {"id": None, "name": "Uncategorized", "color": "muted", "icon": "help-circle", "parent_id": None, "total": 100.0, "count": 3, "pct": 25.0},
        ])
        self.assertEqual(q.call_args.args[1][0], 1)  # user scoped

    def test_csv_format(self):
        with mock.patch.object(DB, "query", return_value=CAT_ROWS, create=True):
            resp = self._get("/api/reports/by-category?format=csv")
        self.assertEqual(resp.mimetype, "text/csv")
        lines = resp.get_data(as_text=True).strip().splitlines()
        self.assertEqual(lines[0], "name,total,count,pct,parent_id,id")
        self.assertEqual(lines[1], "Dining,300.0,12,75.0,,4")

    def test_account_filter_is_passed_to_sql(self):
        with mock.patch.object(DB, "query", return_value=[], create=True) as q:
            self._get("/api/reports/by-category?account_id=2,5")
        sql, params = q.call_args.args
        self.assertIn("t.account_id = ANY(%s)", sql)
        self.assertEqual(params[-1], [2, 5])


class MonthlyPivotTest(unittest.TestCase):
    def test_top_n_and_other_fold(self):
        rows = []
        for cid in range(1, 10):
            rows.append({"month": "2026-09", "category_id": cid, "name": f"Cat{cid}", "color": "c1", "total": Decimal(str(cid * 10))})
        with mock.patch.object(DB, "query", return_value=rows, create=True), mock.patch.object(reports, "today", return_value=NOW):
            data = reports.monthly_by_category(1, months=2, top_n=7)
        self.assertEqual(data["months"], ["2026-08", "2026-09"])
        self.assertEqual([s["name"] for s in data["series"]], ["Cat9", "Cat8", "Cat7", "Cat6", "Cat5", "Cat4", "Cat3", "Other"])
        self.assertEqual(data["series"][-1]["values"], [0.0, 30.0])
        self.assertEqual(data["totals"], [0.0, 450.0])


if __name__ == "__main__":
    unittest.main()


class RecurringReportTest(unittest.TestCase):
    def test_recurring_uses_detector_and_drops_dismissed(self):
        from datetime import date as _d
        rows = [{"id": i, "merchant_key": "NETFLIX", "merchant_name": "Netflix", "account_id": 1,
                 "txn_date": _d(2026, 3 + i, 15), "amount": Decimal("-15.99"), "category_id": 7} for i in range(6)]
        rows += [{"id": 50 + i, "merchant_key": "GYM", "merchant_name": "Gym", "account_id": 1,
                  "txn_date": _d(2026, 3 + i, 1), "amount": Decimal("-40"), "category_id": None} for i in range(6)]

        def fake_query(sql, params=None, one=False, **k):
            if "recurring_dismissals" in sql:
                return [{"merchant_key": "GYM"}]
            if "anomaly_dismissals" in sql:
                return []
            if "FROM categories" in sql:
                return [{"id": 7, "name": "Streaming", "color": "c12", "icon": "tv"}]
            return rows

        with mock.patch.object(DB, "query", side_effect=fake_query, create=True), mock.patch.object(reports, "today", return_value=NOW):
            out = reports.recurring(1)
            anomalies = reports.anomalies(1)
        self.assertEqual([(s["merchant_key"], s["category_name"], s["next_expected"]) for s in out],
                         [("NETFLIX", "Streaming", "2026-09-14")])
        self.assertEqual(anomalies, [])


class IncludeTransfersTest(unittest.TestCase):
    """include_transfers drops the transfer/excluded conditions from every spending query."""

    CALLS = [
        ("by_category", lambda inc: reports.by_category(1, date(2026, 8, 1), date(2026, 8, 31), "top", None, include_transfers=inc)),
        ("monthly", lambda inc: reports.monthly_by_category(1, 3, None, include_transfers=inc)),
        ("trends", lambda inc: reports.trends(1, 3, None, include_transfers=inc)),
        ("top_merchants", lambda inc: reports.top_merchants(1, date(2026, 8, 1), date(2026, 8, 31), 5, None, include_transfers=inc)),
        ("month_over_month", lambda inc: reports.month_over_month(1, "2026-08", None, include_transfers=inc)),
    ]

    def _sql(self, fn, inc):
        with mock.patch.object(DB, "query", return_value=[], create=True) as q, \
                mock.patch.object(reports, "today", return_value=NOW):
            fn(inc)
        return " ".join(q.call_args_list[0].args[0].split())

    def test_default_excludes_transfers_and_excluded_rows(self):
        for name, fn in self.CALLS:
            with self.subTest(report=name):
                self.assertIn("NOT t.is_transfer AND NOT t.is_excluded", self._sql(fn, False))

    def test_include_transfers_counts_them(self):
        for name, fn in self.CALLS:
            with self.subTest(report=name):
                sql = self._sql(fn, True)
                self.assertNotIn("is_transfer", sql)
                self.assertNotIn("is_excluded", sql)
                self.assertIn("t.user_id = %s", sql)

    def test_summary_case_expressions_follow_the_flag(self):
        with mock.patch.object(DB, "query", return_value={"income": 0, "expenses": 0, "txn_count": 0,
                                                          "uncategorized": 0, "transfers": 0, "suggested": 0}, create=True) as q:
            reports.summary(1, date(2026, 8, 1), date(2026, 8, 31), include_transfers=True)
            inc_sql = " ".join(q.call_args.args[0].split())
            reports.summary(1, date(2026, 8, 1), date(2026, 8, 31))
            dflt_sql = " ".join(q.call_args.args[0].split())
        self.assertIn("CASE WHEN t.amount > 0 THEN t.amount END", inc_sql)
        self.assertIn("CASE WHEN t.amount > 0 AND NOT t.is_transfer AND NOT t.is_excluded THEN t.amount END", dflt_sql)

    def test_endpoint_param_reaches_the_query(self):
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(reports_api.bp)
        for path, expect_excluded in (("/api/reports/by-category?include_transfers=1", False),
                                      ("/api/reports/by-category", True)):
            with app.test_request_context(path), mock.patch.object(DB, "query", return_value=[], create=True) as q:
                session["user_id"] = 1
                reports_api.by_category()
            sql = " ".join(q.call_args.args[0].split())
            self.assertEqual("NOT t.is_transfer" in sql, expect_excluded, path)


class FlowTest(unittest.TestCase):
    CALLS = [
        ("by_category", lambda flow: reports.by_category(1, date(2026, 8, 1), date(2026, 8, 31), "top", None, flow=flow)),
        ("monthly_by_category", lambda flow: reports.monthly_by_category(1, 3, None, flow=flow)),
        ("top_merchants", lambda flow: reports.top_merchants(1, date(2026, 8, 1), date(2026, 8, 31), 5, None, flow=flow)),
        ("month_over_month", lambda flow: reports.month_over_month(1, "2026-08", None, flow=flow)),
    ]

    def _sql(self, fn, flow):
        with mock.patch.object(DB, "query", return_value=[], create=True) as q, \
                mock.patch.object(reports, "today", return_value=NOW):
            fn(flow)
        return " ".join(q.call_args_list[0].args[0].split())

    def test_income_flow_sums_positive_amounts(self):
        for name, fn in self.CALLS:
            with self.subTest(report=name):
                sql = self._sql(fn, "income")
                self.assertIn("t.amount > 0", sql)
                self.assertNotIn("-t.amount", sql)
                self.assertIn("NOT t.is_transfer AND NOT t.is_excluded", sql)

    def test_default_flow_is_spending(self):
        for name, fn in self.CALLS:
            with self.subTest(report=name):
                sql = self._sql(fn, "spending")
                self.assertIn("t.amount < 0", sql)
                self.assertRegex(sql, r"-(t\.amount|COALESCE\(s\.amount, t\.amount\))")

    def test_endpoint_flow_param(self):
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(reports_api.bp)
        for path, expect in (("/api/reports/top-merchants?flow=income", "t.amount > 0"),
                             ("/api/reports/top-merchants?flow=bogus", "t.amount < 0"),
                             ("/api/reports/by-category?flow=income", "t.amount > 0")):
            with app.test_request_context(path), mock.patch.object(DB, "query", return_value=[], create=True) as q:
                session["user_id"] = 1
                out = reports_api.top_merchants() if "merchants" in path else reports_api.by_category()
            self.assertIn(expect, " ".join(q.call_args_list[0].args[0].split()), path)
            self.assertEqual(json.loads(out.get_data())["flow"], "income" if "flow=income" in path else "spending")
