import unittest
from datetime import date

import budgets


ROWS = [
    {"id": 1, "name": "Dining", "parent_id": None, "total": 100.0, "color": "c2", "icon": "utensils"},
    {"id": 2, "name": "Coffee", "parent_id": 1, "total": 40.0, "color": "c2", "icon": "coffee"},
    {"id": 3, "name": "Bars", "parent_id": 1, "total": 10.0, "color": "c2", "icon": "beer"},
    {"id": 5, "name": "Groceries", "parent_id": None, "total": 200.0, "color": "c3", "icon": "cart"},
    {"id": 9, "name": "Fuel", "parent_id": 8, "total": 30.0, "color": "c4", "icon": "fuel"},
    {"id": None, "name": None, "parent_id": None, "total": 7.0, "color": None, "icon": None},
]
CATS = {1: {"name": "Dining", "color": "c2", "icon": "utensils", "parent_id": None},
        2: {"name": "Coffee", "color": "c2", "icon": "coffee", "parent_id": 1},
        3: {"name": "Bars", "color": "c2", "icon": "beer", "parent_id": 1},
        5: {"name": "Groceries", "color": "c3", "icon": "cart", "parent_id": None},
        8: {"name": "Transport", "color": "c4", "icon": "car", "parent_id": None},
        9: {"name": "Fuel", "color": "c4", "icon": "fuel", "parent_id": 8}}
SEPT = (date(2026, 9, 1), date(2026, 9, 30))


class ElapsedFractionTest(unittest.TestCase):
    def test_before_during_after(self):
        self.assertEqual(budgets.elapsed_fraction(*SEPT, date(2026, 8, 31)), 0.0)
        self.assertEqual(budgets.elapsed_fraction(*SEPT, date(2026, 9, 1)), round(1 / 30, 4))
        self.assertEqual(budgets.elapsed_fraction(*SEPT, date(2026, 9, 15)), 0.5)
        self.assertEqual(budgets.elapsed_fraction(*SEPT, date(2026, 9, 30)), 1.0)
        self.assertEqual(budgets.elapsed_fraction(*SEPT, date(2026, 10, 1)), 1.0)

    def test_leap_february(self):
        self.assertEqual(budgets.elapsed_fraction(date(2024, 2, 1), date(2024, 2, 29), date(2024, 2, 29)), 1.0)
        self.assertEqual(budgets.elapsed_fraction(date(2024, 2, 1), date(2024, 2, 29), date(2024, 2, 14)), round(14 / 29, 4))


class RollupTest(unittest.TestCase):
    def test_parent_includes_children_and_skips_uncategorized(self):
        self.assertEqual(budgets.rollup(ROWS), {1: 150.0, 2: 40.0, 3: 10.0, 5: 200.0, 8: 30.0, 9: 30.0})

    def test_child_only_parent(self):
        self.assertEqual(budgets.rollup([{"id": 9, "parent_id": 8, "total": 12.5}]), {9: 12.5, 8: 12.5})


class PaceTest(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(budgets.pace_status(101, 100, 0.5), "over")
        self.assertEqual(budgets.pace_status(100, 100, 0.5), "ahead")
        self.assertEqual(budgets.pace_status(61, 100, 0.5), "ahead")
        self.assertEqual(budgets.pace_status(60, 100, 0.5), "on_track")
        self.assertEqual(budgets.pace_status(95, 100, 1.0), "on_track")
        self.assertEqual(budgets.pace_status(5, 0, 0.5), "on_track")

    def test_projection(self):
        self.assertIsNone(budgets.projected(50, 0.0))
        self.assertIsNone(budgets.projected(50, 1.0))
        self.assertEqual(budgets.projected(50, 0.25), 200.0)


class BuildProgressTest(unittest.TestCase):
    def test_items_totals_and_unbudgeted(self):
        out = budgets.build_progress([{"id": 10, "category_id": 1, "amount": 200, "note": None},
                                      {"id": 11, "category_id": 9, "amount": 20, "note": "petrol"}], ROWS, 0.5, CATS)
        self.assertEqual([(i["category_id"], i["spent"], i["remaining"], i["pct"], i["pace_status"], i["projected"]) for i in out["items"]],
                         [(9, 30.0, -10.0, 150.0, "over", 60.0), (1, 150.0, 50.0, 75.0, "ahead", 300.0)])
        self.assertEqual(out["items"][0]["name"], "Fuel")
        self.assertEqual(out["totals"], {"budget": 220.0, "spent": 180.0, "remaining": 40.0, "pct": 81.8, "pace_status": "ahead"})
        # Groceries has no budget; Transport's only spending sits in the budgeted Fuel child; Dining is budgeted
        self.assertEqual(out["unbudgeted"], {"spent": 200.0, "count": 1, "categories": [{"id": 5, "name": "Groceries", "color": "c3", "icon": "cart", "total": 200.0}]})

    def test_empty(self):
        self.assertEqual(budgets.build_progress([], [], 0.3, {}), {"items": [], "totals": {"budget": 0.0, "spent": 0.0, "remaining": 0.0, "pct": 0.0, "pace_status": "on_track"},
                                                                    "unbudgeted": {"spent": 0.0, "count": 0, "categories": []}})

    def test_month_meta(self):
        self.assertEqual(budgets.month_meta(*SEPT, date(2026, 9, 7)), {"start": "2026-09-01", "end": "2026-09-30", "days_in_month": 30, "days_elapsed": 7, "elapsed_pct": 23.3})
        self.assertEqual(budgets.month_meta(*SEPT, date(2026, 12, 1))["days_elapsed"], 30)


if __name__ == "__main__":
    unittest.main()
