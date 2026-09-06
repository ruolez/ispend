import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tests import _stubs  # noqa: E402

_stubs.install()

import ai_extract  # noqa: E402
import openrouter  # noqa: E402


class ExtractRowsTest(unittest.TestCase):
    def test_rows_are_staged_with_problems_for_bad_fields(self):
        payload = {"rows": [
            {"date": "2026-05-01", "description": "ZELLE MATVEY", "amount": 40, "balance": 482.53},
            {"date": "bad", "description": "PAYPAL", "amount": "-10.00"},
            {"date": "2026-05-02", "description": "", "amount": 5},
        ]}
        with mock.patch.object(openrouter, "chat_json", return_value=(payload, {})), \
             mock.patch.object(openrouter, "log_call"), mock.patch.object(openrouter, "model", return_value="m"):
            rows, calls = ai_extract.extract_rows(1, [(1, "some statement text long enough")], period=(date(2026, 4, 9), date(2026, 5, 8)))
        self.assertEqual(calls, 1)
        self.assertEqual([(r.txn_date, r.amount, r.description, r.balance, r.problems) for r in rows], [
            (date(2026, 5, 1), Decimal("40.00"), "ZELLE MATVEY", Decimal("482.53"), []),
            (None, Decimal("-10.00"), "PAYPAL", None, ["AI could not read the date"]),
        ])

    def test_short_pages_are_skipped_without_a_call(self):
        with mock.patch.object(openrouter, "chat_json") as cj:
            rows, calls = ai_extract.extract_rows(1, [(1, "hi")])
        self.assertEqual((rows, calls, cj.called), ([], 0, False))
