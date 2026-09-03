import unittest
from datetime import date, timedelta
from decimal import Decimal

import recurring

TODAY = date(2026, 9, 3)


def txn(i, key, d, amount, name=None, account_id=1, category_id=None):
    return {"id": i, "merchant_key": key, "merchant_name": name or key.title(), "account_id": account_id,
            "txn_date": d, "amount": Decimal(str(amount)), "category_id": category_id}


def monthly(key, amount, start, n, day=15, amounts=None):
    rows = []
    for i in range(n):
        y, m = start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1
        amt = amounts[i] if amounts else amount
        rows.append(txn(100 + i, key, date(y, m, day), amt))
    return rows


class DetectTest(unittest.TestCase):
    def test_monthly_fixed_subscription(self):
        rows = monthly("NETFLIX", -15.99, date(2026, 3, 15), 6)
        for r in rows:
            r["category_id"] = 7
        out = recurring.detect(rows, TODAY)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual((s["merchant_key"], s["cadence"], s["amount_kind"], s["occurrences"]),
                         ("NETFLIX", "monthly", "fixed", 6))
        self.assertEqual(s["median_amount"], 15.99)
        self.assertEqual(s["monthly_equivalent"], 15.99)
        self.assertEqual(s["last_date"], date(2026, 8, 15))
        self.assertEqual(s["next_expected"], date(2026, 9, 14))
        self.assertTrue(s["is_active"])
        self.assertEqual(s["category_id"], 7)
        self.assertEqual(s["ids"], [100, 101, 102, 103, 104, 105])

    def test_income_is_ignored(self):
        rows = [txn(i, "PAYROLL", date(2026, 1, 2) + timedelta(days=14 * i), 2500) for i in range(8)]
        self.assertEqual(recurring.detect(rows, TODAY), [])

    def test_variable_monthly_utility(self):
        rows = monthly("COMED", None, date(2026, 3, 3), 6, day=3, amounts=[-80, -95, -110, -88, -102, -91])
        out = recurring.detect(rows, TODAY)
        self.assertEqual([(s["cadence"], s["amount_kind"]) for s in out], [("monthly", "variable")])

    def test_irregular_intervals_rejected(self):
        rows = [txn(1, "SHOP", date(2026, 1, 1), -20), txn(2, "SHOP", date(2026, 1, 9), -20),
                txn(3, "SHOP", date(2026, 3, 20), -20), txn(4, "SHOP", date(2026, 6, 1), -20)]
        self.assertEqual(recurring.detect(rows, TODAY), [])

    def test_wildly_varying_amounts_rejected(self):
        rows = monthly("AMAZON", None, date(2026, 3, 10), 5, day=10, amounts=[-5, -300, -12, -900, -40])
        self.assertEqual(recurring.detect(rows, TODAY), [])

    def test_inactive_when_stale(self):
        rows = monthly("GYM", -40, date(2025, 9, 1), 4, day=1)
        out = recurring.detect(rows, TODAY)
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0]["is_active"])

    def test_yearly_and_weekly_equivalents(self):
        yearly = [txn(i, "DOMAIN", date(2023 + i, 5, 20), -120) for i in range(4)]
        weekly = [txn(10 + i, "LAUNDRY", date(2026, 7, 1) + timedelta(days=7 * i), -10) for i in range(9)]
        out = {s["merchant_key"]: s for s in recurring.detect(yearly + weekly, TODAY)}
        self.assertEqual(out["DOMAIN"]["cadence"], "yearly")
        self.assertEqual(out["DOMAIN"]["monthly_equivalent"], 10.0)
        self.assertEqual(out["LAUNDRY"]["cadence"], "weekly")
        self.assertEqual(out["LAUNDRY"]["monthly_equivalent"], round(10 * 52 / 12, 2))

    def test_min_occurrences(self):
        rows = monthly("SPOTIFY", -9.99, date(2026, 7, 1), 2)
        self.assertEqual(recurring.detect(rows, TODAY), [])
        self.assertEqual(len(recurring.detect(rows, TODAY, min_occurrences=2)), 1)


class AnomaliesTest(unittest.TestCase):
    def test_unusual_amount(self):
        rows = monthly("COMED", None, date(2026, 3, 3), 7, day=3, amounts=[-80, -85, -82, -84, -83, -81, -300])
        out = recurring.anomalies(rows, TODAY)
        self.assertEqual([(a["kind"], a["transaction_id"], a["amount"]) for a in out],
                         [("unusual_amount", 106, 300.0)])
        self.assertEqual(out[0]["delta"], 300.0 - 82.5)

    def test_new_merchant_only_when_large(self):
        rows = [txn(1, "BESTBUY", date(2026, 8, 30), -450), txn(2, "CORNER STORE", date(2026, 8, 30), -12)]
        out = recurring.anomalies(rows, TODAY)
        self.assertEqual([(a["kind"], a["transaction_id"]) for a in out], [("new_merchant", 1)])

    def test_duplicate_charge_same_day(self):
        rows = [txn(1, "UBER", date(2026, 9, 1), -23.5), txn(2, "UBER", date(2026, 9, 1), -23.5),
                txn(3, "UBER", date(2026, 9, 1), -8.0)]
        out = recurring.anomalies(rows, TODAY)
        self.assertEqual([(a["kind"], a["transaction_id"], a["duplicate_of"]) for a in out],
                         [("duplicate_charge", 2, 1)])

    def test_outside_lookback_ignored(self):
        rows = [txn(1, "BESTBUY", date(2026, 1, 1), -450)]
        self.assertEqual(recurring.anomalies(rows, TODAY), [])


if __name__ == "__main__":
    unittest.main()
