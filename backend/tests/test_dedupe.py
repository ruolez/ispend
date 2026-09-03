import hashlib
import unittest
from datetime import date
from decimal import Decimal

import dedupe


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_is_sha1_of_canonical_fields(self):
        want = hashlib.sha1(b"7|2024-08-05|-84.12|WHOLEFDS LKV").hexdigest()
        self.assertEqual(dedupe.fingerprint(7, date(2024, 8, 5), Decimal("-84.12"), "WHOLEFDS LKV"), want)

    def test_amount_normalised_to_two_places(self):
        self.assertEqual(dedupe.fingerprint(1, date(2024, 1, 1), Decimal("5"), "X"),
                         dedupe.fingerprint(1, date(2024, 1, 1), Decimal("5.00"), "X"))

    def test_account_changes_fingerprint(self):
        self.assertNotEqual(dedupe.fingerprint(1, date(2024, 1, 1), Decimal("5"), "X"),
                            dedupe.fingerprint(2, date(2024, 1, 1), Decimal("5"), "X"))


class OccurrenceTest(unittest.TestCase):
    def test_same_day_identical_rows_get_incrementing_occurrence(self):
        rows = [{"fingerprint": "a"}, {"fingerprint": "b"}, {"fingerprint": "a"}, {"fingerprint": None}, {"fingerprint": "a"}]
        dedupe.assign_occurrences(rows)
        self.assertEqual([r.get("occurrence") for r in rows], [1, 1, 2, None, 3])

    def test_in_file_duplicates_detects_repeated_pairs(self):
        rows = [{"fingerprint": "a", "occurrence": 1}, {"fingerprint": "a", "occurrence": 1},
                {"fingerprint": "a", "occurrence": 2}]
        self.assertEqual(dedupe.in_file_duplicates(rows), {1})


if __name__ == "__main__":
    unittest.main()
