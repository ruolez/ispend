import os
import sys
import unittest
from unittest import mock
from datetime import date, datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

_stubs.install()

import util  # noqa: E402


class RowJsonTest(unittest.TestCase):
    def test_converts_decimals_and_dates_and_keeps_the_rest(self):
        row = {"amount": Decimal("-12.50"), "day": date(2026, 1, 2),
               "at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc), "name": "x", "n": None, "flag": True}
        self.assertEqual(util.row_json(row), {
            "amount": -12.5, "day": "2026-01-02", "at": "2026-01-02T03:04:05+00:00", "name": "x", "n": None, "flag": True,
        })

    def test_rows_json_maps_each_row(self):
        self.assertEqual(util.rows_json([{"v": Decimal("1")}, {"v": Decimal("2.25")}]), [{"v": 1.0}, {"v": 2.25}])

    def test_money_and_iso(self):
        self.assertEqual((util.money(None), util.money(Decimal("3.10"))), (None, 3.1))
        self.assertEqual((util.iso(None), util.iso(date(2026, 5, 6)), util.iso(7)), (None, "2026-05-06", "7"))


class ParseIntListTest(unittest.TestCase):
    def test_csv_string_list_and_junk(self):
        self.assertEqual(util.parse_int_list("1,2,x,3"), [1, 2, 3])
        self.assertEqual(util.parse_int_list([1, "2", None, 3.7, "abc"]), [1, 2, 3])
        self.assertEqual(util.parse_int_list(None), [])
        self.assertEqual(util.parse_int_list(""), [])


class RecordEventsTest(unittest.TestCase):
    def test_bulk_events_serialize_detail(self):
        fake = _stubs.FakeDB()
        with mock.patch.object(util, "db", fake):
            util.record_events([(5, "manual", {"category_id": 3}, 1), (6, "imported", None, None)])
            util.record_events([])
        kind, sql, rows = fake.calls[-1]
        self.assertEqual(kind, "execute_values")
        self.assertIn("INSERT INTO transaction_events", sql)
        self.assertEqual(rows, [(5, "manual", '{"category_id": 3}', 1), (6, "imported", None, None)])
        self.assertEqual(len(fake.calls), 1)


if __name__ == "__main__":
    unittest.main()
