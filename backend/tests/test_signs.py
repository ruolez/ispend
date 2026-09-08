import contextlib
import os
import sys
import types
import unittest
from unittest import mock
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tests import _stubs  # noqa: E402

_stubs.install()

import dedupe  # noqa: E402
import signs  # noqa: E402
import util  # noqa: E402
from importer.generic import looks_inverted  # noqa: E402


def row(i, amount, desc="COFFEE", day=1):
    return {"id": i, "account_id": 7, "txn_date": date(2026, 5, day), "amount": Decimal(amount),
            "description_clean": desc, "fingerprint": "old", "occurrence": 1}


class FlipSignsDbTest(unittest.TestCase):
    def test_split_lines_are_negated_with_the_parent(self):
        calls = []

        def query(sql, params=None, **kw):
            calls.append(("query", sql, params))
            return [row(1, "12.50")] if "FROM transactions WHERE user_id" in sql else []

        def execute(sql, params=None, **kw):
            calls.append(("execute", sql, params))
            return 1
        fake = types.SimpleNamespace(query=query, execute=execute, transaction=contextlib.nullcontext,
                                     execute_values=lambda sql, rows, **kw: calls.append(("execute_values", sql, rows)))
        with mock.patch.object(signs, "db", fake), mock.patch.object(util, "db", fake):
            self.assertEqual(signs.flip_signs(1, [1]), 1)
        self.assertIn(("execute", "UPDATE transaction_splits SET amount = -amount WHERE transaction_id = ANY(%s)", ([1],)), calls)
        self.assertEqual(calls[-1][0], "execute_values")


class PlanFlipsTest(unittest.TestCase):
    def test_amounts_negated_and_fingerprints_recomputed(self):
        plan = signs.plan_flips([row(1, "12.50"), row(2, "-3.00", "REFUND")], set())
        self.assertEqual(plan, [
            (1, Decimal("-12.50"), dedupe.fingerprint(7, date(2026, 5, 1), Decimal("-12.50"), "COFFEE"), 1),
            (2, Decimal("3.00"), dedupe.fingerprint(7, date(2026, 5, 1), Decimal("3.00"), "REFUND"), 1),
        ])

    def test_same_day_identical_rows_get_distinct_occurrences(self):
        plan = signs.plan_flips([row(1, "5.00"), row(2, "5.00")], set())
        self.assertEqual([p[3] for p in plan], [1, 2])
        self.assertEqual(plan[0][2], plan[1][2])

    def test_skips_pairs_taken_by_untouched_rows(self):
        fp = dedupe.fingerprint(7, date(2026, 5, 1), Decimal("-5.00"), "COFFEE")
        plan = signs.plan_flips([row(1, "5.00")], {(fp, 1), (fp, 2)})
        self.assertEqual(plan[0][2:], (fp, 3))


class LooksInvertedTest(unittest.TestCase):
    MOSTLY_POSITIVE = [Decimal("12"), Decimal("30"), Decimal("4"), Decimal("99"), Decimal("7"), Decimal("-500")]

    def test_card_with_mostly_positive_amounts_is_inverted(self):
        self.assertTrue(looks_inverted(self.MOSTLY_POSITIVE, "credit_card"))

    def test_checking_and_savings_are_never_judged(self):
        for kind in ("checking", "savings", "cash", None):
            self.assertFalse(looks_inverted(self.MOSTLY_POSITIVE, kind))

    def test_too_few_rows_or_balanced_signs_are_not_inverted(self):
        self.assertFalse(looks_inverted(self.MOSTLY_POSITIVE[:3], "credit_card"))
        balanced = [Decimal("-10"), Decimal("-20"), Decimal("5"), Decimal("-3"), Decimal("8"), Decimal("-9")]
        self.assertFalse(looks_inverted(balanced, "credit_card"))
        self.assertFalse(looks_inverted([None, Decimal("0")] * 5, "credit_card"))


if __name__ == "__main__":
    unittest.main()
