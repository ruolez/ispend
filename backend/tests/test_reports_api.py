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
