import os
import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

import reports  # noqa: E402


JAN15 = date(2026, 1, 15)


class ResolveRangeBoundaryTest(unittest.TestCase):
    """Presets computed mid-January must roll correctly across the month and year boundary."""

    def test_calendar_presets_cross_the_year(self):
        cases = {
            "this-month": (date(2026, 1, 1), date(2026, 1, 31), date(2025, 12, 1), date(2025, 12, 31)),
            "last-month": (date(2025, 12, 1), date(2025, 12, 31), date(2025, 11, 1), date(2025, 11, 30)),
            "this-year": (date(2026, 1, 1), date(2026, 12, 31), date(2025, 1, 1), date(2025, 12, 31)),
            "last-year": (date(2025, 1, 1), date(2025, 12, 31), date(2024, 1, 1), date(2024, 12, 31)),
        }
        for name, expected in cases.items():
            r = reports.resolve_range(name, now=JAN15)
            self.assertEqual((r["start"], r["end"], r["prev_start"], r["prev_end"]), expected, name)
            self.assertEqual((r["name"], r["label"]), (name, reports.RANGE_LABELS[name]))

    def test_rolling_windows_are_contiguous_with_their_previous_window(self):
        for name, days in (("last-30", 30), ("last-90", 90)):
            r = reports.resolve_range(name, now=JAN15)
            self.assertEqual(r["end"], JAN15, name)
            self.assertEqual(r["start"], JAN15 - timedelta(days=days - 1), name)
            self.assertEqual(r["prev_end"], r["start"] - timedelta(days=1), name)
            self.assertEqual(r["prev_start"], r["prev_end"] - timedelta(days=days - 1), name)

    def test_leap_february(self):
        r = reports.resolve_range("last-month", now=date(2024, 3, 10))
        self.assertEqual((r["start"], r["end"]), (date(2024, 2, 1), date(2024, 2, 29)))

    def test_custom_range_previous_window_has_the_same_length(self):
        r = reports.resolve_range(None, "2026-01-05", "2026-01-14", now=JAN15)
        self.assertEqual((r["name"], r["prev_start"], r["prev_end"]), ("custom", date(2025, 12, 26), date(2026, 1, 4)))

    def test_unparseable_custom_dates_widen_to_everything_up_to_today(self):
        r = reports.resolve_range(None, "nope", None, now=JAN15)
        self.assertEqual((r["name"], r["start"], r["end"]), ("custom", reports.ALL_START, JAN15))

    def test_all_time_has_no_previous_window(self):
        r = reports.resolve_range("all", now=JAN15)
        self.assertEqual((r["start"], r["end"], r["prev_start"], r["prev_end"]),
                         (reports.ALL_START, reports.ALL_END, None, None))


class AcctFilterTest(unittest.TestCase):
    def test_builder(self):
        self.assertEqual(reports._acct(None), ("", []))
        self.assertEqual(reports._acct([]), ("", []))
        self.assertEqual(reports._acct((2, 5)), (" AND t.account_id = ANY(%s)", [[2, 5]]))


def _cat(i, total):
    return {"id": i, "name": f"Cat{i}", "color": "c1", "icon": "tag", "parent_id": None,
            "total": total, "count": i, "pct": 10.0}


class DashboardAssemblyTest(unittest.TestCase):
    def test_dashboard_folds_extra_categories_and_assembles_kpis(self):
        cur = {"expenses": 500.0, "income": 900.0, "net": 400.0, "txn_count": 12}
        prev = {"expenses": 250.0, "income": 800.0, "net": 550.0, "txn_count": 9}
        cats = [_cat(i, float(100 - i)) for i in range(1, 9)]  # 8 categories -> 6 + Other
        trend = [{"month": "2026-01", "expenses": 500.0, "income": 900.0, "net": 400.0, "count": 12}]
        router = _stubs.Router([
            ("FILTER (WHERE category_id IS NULL", {"uncategorized": 3, "suggested": 2}),
            ("FROM accounts a WHERE a.user_id", [{"id": 1, "name": "Card", "currency": "USD", "account_type": "credit_card",
                                                  "color": "c1", "balance": Decimal("-10.00"), "last_txn_date": date(2026, 1, 10)}]),
            ("ORDER BY t.txn_date DESC, t.id DESC LIMIT 10", [{"id": 9, "txn_date": date(2026, 1, 10), "amount": Decimal("-5.00")}]),
        ])
        with mock.patch.object(reports, "summary", side_effect=[cur, prev]) as summ, \
                mock.patch.object(reports, "by_category", return_value=cats), \
                mock.patch.object(reports, "trends", return_value=trend), \
                mock.patch.object(reports, "recurring_summary", return_value={"count": 1, "monthly_total": 9.99, "next": []}), \
                mock.patch.object(reports, "today", return_value=JAN15), \
                mock.patch.object(reports, "db", FAKE), mock.patch.object(FAKE, "query", side_effect=router):
            out = reports.dashboard(7, "this-month", [1])
        self.assertEqual(summ.call_args_list[0].args, (7, date(2026, 1, 1), date(2026, 1, 31), [1]))
        self.assertEqual(out["kpis"], {"spent": 500.0, "spent_prev": 250.0, "income": 900.0, "income_prev": 800.0,
                                       "net": 400.0, "net_prev": 550.0, "txn_count": 12})
        self.assertEqual([c["name"] for c in out["top_categories"]], ["Cat1", "Cat2", "Cat3", "Cat4", "Cat5", "Cat6", "Other"])
        self.assertEqual(out["top_categories"][-1]["total"], 93.0 + 92.0)
        self.assertEqual(out["top_categories"][-1]["count"], 7 + 8)
        self.assertEqual(out["monthly"], [{"month": "2026-01", "spent": 500.0, "income": 900.0, "net": 400.0}])
        self.assertEqual(out["review_count"], {"uncategorized": 3, "suggested": 2})
        self.assertEqual(out["accounts"][0]["balance"], -10.0)
        self.assertEqual(out["recent"][0]["amount"], -5.0)
        self.assertEqual(out["range"]["start"], "2026-01-01")
        recent_sql, recent_params = router.sql("LIMIT 10")[0]
        self.assertIn("t.account_id = ANY(%s)", recent_sql)
        self.assertEqual(recent_params, (7, [1]))

    def test_dashboard_without_previous_period_reports_none(self):
        cur = {"expenses": 1.0, "income": 0.0, "net": -1.0, "txn_count": 1}
        router = _stubs.Router([("FILTER (WHERE category_id IS NULL", {"uncategorized": 0, "suggested": 0})])
        with mock.patch.object(reports, "summary", return_value=cur), \
                mock.patch.object(reports, "by_category", return_value=[]), \
                mock.patch.object(reports, "trends", return_value=[]), \
                mock.patch.object(reports, "recurring_summary", return_value={}), \
                mock.patch.object(reports, "today", return_value=JAN15), \
                mock.patch.object(reports, "db", FAKE), mock.patch.object(FAKE, "query", side_effect=router):
            out = reports.dashboard(7, "all")
        self.assertEqual((out["kpis"]["spent_prev"], out["kpis"]["net_prev"], out["top_categories"]), (None, None, []))


if __name__ == "__main__":
    unittest.main()
