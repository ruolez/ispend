import unittest
from decimal import Decimal

import splits

OK = [{"category_id": 3, "amount": "-30", "note": " lunch  for two "}, {"category_id": "4", "amount": -20.004}]


class ValidateSplitsTest(unittest.TestCase):
    def test_clean_lines_are_normalised(self):
        clean, err = splits.validate_splits("-50.00", OK)
        self.assertIsNone(err)
        self.assertEqual(clean, [{"category_id": 3, "amount": Decimal("-30.00"), "note": "lunch for two"},
                                 {"category_id": 4, "amount": Decimal("-20.00"), "note": None}])

    def test_rejections(self):
        cases = [
            ("0", OK, "Only transactions with an amount can be split"),
            ("-50", "x", "lines must be a list"),
            ("-50", OK[:1], "A split needs at least two lines"),
            ("-50", [{"category_id": 1, "amount": -1}] * 21, "A split can have at most 20 lines"),
            ("-50", [OK[0], "x"], "Line 2 is not an object"),
            ("-50", [OK[0], {"amount": -20}], "Line 2 needs a category"),
            ("-50", [OK[0], {"category_id": "abc", "amount": -20}], "Line 2 needs a category"),
            ("-50", [OK[0], {"category_id": 4, "amount": "abc"}], "Line 2 needs an amount"),
            ("-50", [OK[0], {"category_id": 4, "amount": 0}], "Line 2 cannot be zero"),
            ("-50", [OK[0], {"category_id": 4, "amount": 20}], "Line 2 must be negative like the transaction"),
            ("50", [{"category_id": 3, "amount": 30}, {"category_id": 4, "amount": -20}], "Line 2 must be positive like the transaction"),
            ("-50", [OK[0], {"category_id": 4, "amount": -20, "note": "n" * 201}], "Line 2 note must be at most 200 characters"),
            ("-50", [OK[0], {"category_id": 4, "amount": -19.99}], "Lines add up to -49.99 but the transaction is -50.00 (-0.01 left)"),
            ("1234.5", [{"category_id": 3, "amount": 1000}, {"category_id": 4, "amount": 200}], "Lines add up to 1,200.00 but the transaction is 1,234.50 (34.50 left)"),
        ]
        for parent, lines, msg in cases:
            self.assertEqual(splits.validate_splits(parent, lines), (None, msg), (parent, lines))

    def test_remaining(self):
        self.assertEqual(splits.remaining("-50", OK), Decimal("0.00"))
        self.assertEqual(splits.remaining("-50", OK[:1]), Decimal("-20.00"))
        self.assertEqual(splits.remaining("-50", [{"amount": "junk"}, 7]), Decimal("-50.00"))

    def test_summary_text(self):
        self.assertEqual(splits.summary_text([{"category_id": 3, "amount": -30}, {"category_id": 9, "amount": -20}], {3: "Food"}),
                         "Food 30.00 · Uncategorized 20.00")


if __name__ == "__main__":
    unittest.main()
