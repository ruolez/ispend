import os
import unittest
from datetime import date
from decimal import Decimal

from importer import csv_parser, excel_parser

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
WANT = [(date(2024, 8, 1), Decimal("-4.50"), "COFFEE SHOP"), (date(2024, 8, 2), Decimal("3000.00"), "SALARY"),
        (date(2024, 8, 3), Decimal("-120.00"), "GROCERY STORE")]


class ExcelTest(unittest.TestCase):
    def test_xlsx_dates_and_amounts(self):
        rows = excel_parser.load_excel_rows(os.path.join(FIX, "generic.xlsx"), "xlsx")
        result = csv_parser.parse_rows(rows)
        self.assertEqual((rows[0], [(r.txn_date, r.amount, r.description) for r in result.rows]),
                         (["Date", "Description", "Amount"], WANT))

    def test_xls_via_xlrd(self):
        rows = excel_parser.load_excel_rows(os.path.join(FIX, "generic.xls"), "xls")
        result = csv_parser.parse_rows(rows)
        self.assertEqual([(r.txn_date, r.amount, r.description) for r in result.rows], WANT)


if __name__ == "__main__":
    unittest.main()
