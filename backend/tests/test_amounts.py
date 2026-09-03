import unittest
from decimal import Decimal

from importer.amounts import parse_amount

CASES = [
    ("$1,234.56", "1234.56"), ("(12.34)", "-12.34"), ("12.34-", "-12.34"), ("45.00 CR", "45.00"), ("3.00CR", "3.00"),
    ("-$50.00", "-50.00"), ("−12.00", "-12.00"), ("12.00 DR", "-12.00"), ("1234", "1234.00"), ("USD 12.50", "12.50"),
    ("+5.00", "5.00"), (" 7 ", "7.00"), ("CA$ 9.99", "9.99"), ("-1,000", "-1000.00"), ("0.005", "0.01"),
    (12.5, "12.50"), (Decimal("3"), "3.00"), (-3, "-3.00"),
]
NONE_CASES = ["", "   ", None, "abc", "CR", "12/31/2024", "N/A", "--", True]


class ParseAmountTest(unittest.TestCase):
    def test_parses_common_bank_formats(self):
        self.assertEqual([parse_amount(raw) for raw, _ in CASES], [Decimal(want) for _, want in CASES])

    def test_returns_none_for_non_amounts(self):
        self.assertEqual([parse_amount(raw) for raw in NONE_CASES], [None] * len(NONE_CASES))

    def test_always_two_decimal_places(self):
        self.assertEqual(str(parse_amount("1.1")), "1.10")


if __name__ == "__main__":
    unittest.main()
