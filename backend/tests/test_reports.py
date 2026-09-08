import os
import sys
import types
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
        income_cats = [_cat(20 + i, 1000.0 - i) for i in range(8)]
        sources = [{"merchant_key": "GUSTO", "merchant_name": "Gusto", "count": 2, "total": 900.0, "avg": 450.0}]
        with mock.patch.object(reports, "summary", side_effect=[cur, prev]) as summ, \
                mock.patch.object(reports, "by_category", side_effect=[cats, income_cats]) as bycat, \
                mock.patch.object(reports, "top_merchants", return_value=sources) as merch, \
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
        self.assertEqual(out["income"], {"total": 900.0, "previous": 800.0, "categories": income_cats[:6], "sources": sources})
        self.assertEqual(bycat.call_args_list[1].kwargs, {"flow": "income"})
        self.assertEqual(bycat.call_args_list[1].args, (7, date(2026, 1, 1), date(2026, 1, 31), "sub", [1]))
        self.assertEqual(merch.call_args, mock.call(7, date(2026, 1, 1), date(2026, 1, 31), 6, [1], flow="income"))
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
                mock.patch.object(reports, "top_merchants", return_value=[]), \
                mock.patch.object(reports, "trends", return_value=[]), \
                mock.patch.object(reports, "recurring_summary", return_value={}), \
                mock.patch.object(reports, "today", return_value=JAN15), \
                mock.patch.object(reports, "db", FAKE), mock.patch.object(FAKE, "query", side_effect=router):
            out = reports.dashboard(7, "all")
        self.assertEqual((out["kpis"]["spent_prev"], out["kpis"]["net_prev"], out["top_categories"]), (None, None, []))
        self.assertEqual(out["income"], {"total": 0.0, "previous": None, "categories": [], "sources": []})


if __name__ == "__main__":
    unittest.main()


class MonthRangeTest(unittest.TestCase):
    def test_specific_month_with_previous_month_comparison(self):
        r = reports.resolve_range("month:2026-07", now=date(2026, 9, 3))
        self.assertEqual((r["name"], r["label"], r["start"], r["end"], r["prev_start"], r["prev_end"]),
                         ("month:2026-07", "July 2026", date(2026, 7, 1), date(2026, 7, 31), date(2026, 6, 1), date(2026, 6, 30)))

    def test_january_compares_with_december(self):
        r = reports.resolve_range("month:2026-01", now=date(2026, 9, 3))
        self.assertEqual((r["prev_start"], r["prev_end"]), (date(2025, 12, 1), date(2025, 12, 31)))

    def test_invalid_month_falls_back_to_current(self):
        r = reports.resolve_range("month:2026-13", now=date(2026, 9, 3))
        self.assertEqual((r["name"], r["start"]), ("month:2026-09", date(2026, 9, 1)))


class MonthlyDrillDownTest(unittest.TestCase):
    def test_parent_id_restricts_to_subcategories(self):
        calls = []
        fake = types.SimpleNamespace(query=lambda sql, params=None, **kw: calls.append((sql, params)) or [])
        with mock.patch.object(reports, "db", fake):
            out = reports.monthly_by_category(1, 3, parent_id=42)
        sql, params = calls[0]
        self.assertIn("c.parent_id = %s", sql)
        self.assertIn("Directly in", sql)
        self.assertEqual((params[0], params[-1], out["parent_id"], out["series"]), (42, 42, 42, []))


class SplitAttributionSqlTest(unittest.TestCase):
    """Only the category breakdowns read split lines; totals, trends and merchants keep the parent amount."""

    def _capture(self, fn, *args, **kw):
        calls = []

        def query(sql, params=None, one=False, **k):
            calls.append(sql)
            return {"income": 0, "expenses": 0, "txn_count": 0, "uncategorized": 0, "transfers": 0, "suggested": 0} if one else []
        with mock.patch.object(reports, "db", types.SimpleNamespace(query=query)):
            fn(*args, **kw)
        return " ".join(" ".join(calls).split())

    def test_breakdowns_join_the_lines(self):
        for label, sql in (
            ("by_category", self._capture(reports.by_category, 1, JAN15, JAN15)),
            ("monthly", self._capture(reports.monthly_by_category, 1, 3)),
            ("monthly parent", self._capture(reports.monthly_by_category, 1, 3, parent_id=4)),
            ("month_over_month", self._capture(reports.month_over_month, 1, "2026-01")),
        ):
            self.assertIn("LEFT JOIN transaction_splits s ON s.transaction_id = t.id", sql, label)
            self.assertIn("COALESCE(s.category_id, t.category_id)", sql, label)
            self.assertIn("COALESCE(s.amount, t.amount)", sql, label)
            self.assertNotIn("SUM(-t.amount)", sql, label)
        self.assertIn("COUNT(DISTINCT t.id) AS count", self._capture(reports.by_category, 1, JAN15, JAN15))

    def test_other_reports_keep_the_parent_amount(self):
        for label, sql in (
            ("summary", self._capture(reports.summary, 1, JAN15, JAN15)),
            ("trends", self._capture(reports.trends, 1, 3)),
            ("top_merchants", self._capture(reports.top_merchants, 1, JAN15, JAN15)),
            ("largest", self._capture(reports.largest, 1, JAN15, JAN15)),
        ):
            self.assertNotIn("transaction_splits", sql, label)


class CompareMonthsTest(unittest.TestCase):
    def _run(self, **kw):
        calls = []
        fake = types.SimpleNamespace(query=lambda sql, params=None, **k: calls.append((sql, params)) or [])
        with mock.patch.object(reports, "db", fake):
            out = reports.month_over_month(1, "2026-07", **kw)
        return out, calls[0][1]

    def test_default_compares_with_previous_month(self):
        out, params = self._run()
        self.assertEqual((out["month"], out["previous_month"]), ("2026-07", "2026-06"))
        self.assertEqual(params[:4], (date(2026, 7, 1), date(2026, 7, 31), date(2026, 6, 1), date(2026, 6, 30)))

    def test_vs_picks_any_month(self):
        out, params = self._run(vs="2025-12")
        self.assertEqual(out["previous_month"], "2025-12")
        self.assertEqual(params[2:4], (date(2025, 12, 1), date(2025, 12, 31)))

    def test_same_month_falls_back_to_previous(self):
        out, _ = self._run(vs="2026-07")
        self.assertEqual(out["previous_month"], "2026-06")


class ParseMonthBoundsTest(unittest.TestCase):
    def test_out_of_range_years_fall_back_to_the_reference_month(self):
        import reports
        from datetime import date as _date
        ref = _date(2026, 9, 7)
        self.assertEqual(reports.parse_month("99999-01", fallback=ref), (2026, 9))
        self.assertEqual(reports.parse_month("0000-05", fallback=ref), (2026, 9))
        self.assertEqual(reports.parse_month("2024-13", fallback=ref), (2026, 9))
        self.assertEqual(reports.parse_month("2024-02", fallback=ref), (2024, 2))
