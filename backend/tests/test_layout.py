import unittest

from importer import layout

HEADER = ["Post Date", "Transaction Date", "Description", "Amount"]
JULY = [HEADER, ["07/02/2024", "07/01/2024", "COFFEE", "-4.50"], ["07/03/2024", "07/03/2024", "RENT", "-1200.00"]]
AUGUST = [HEADER, ["08/02/2024", "08/01/2024", "GROCER", "-61.10"]]
HEADERLESS = [["08/01/2024", "-45.20", "*", "", "TRADER JOE'S"], ["08/03/2024", "2500.00", "*", "", "PAYROLL"]]


class FingerprintTest(unittest.TestCase):
    def test_same_header_with_different_rows_gives_the_same_key(self):
        self.assertEqual(layout.fingerprint(JULY), layout.fingerprint(AUGUST))

    def test_header_case_and_whitespace_do_not_matter(self):
        shouted = [[" POST  DATE ", "transaction date", "DESCRIPTION", "amount"]] + JULY[1:]
        self.assertEqual(layout.fingerprint(shouted)[0], layout.fingerprint(JULY)[0])

    def test_reordered_columns_give_a_different_key(self):
        swapped = [["Transaction Date", "Post Date", "Description", "Amount"]] + [[r[1], r[0], r[2], r[3]] for r in JULY[1:]]
        self.assertNotEqual(layout.fingerprint(swapped)[0], layout.fingerprint(JULY)[0])

    def test_preamble_rows_do_not_change_the_key(self):
        with_preamble = [["Summary"], ["Beginning balance", "1,000.00"]] + JULY
        self.assertEqual(layout.fingerprint(with_preamble), layout.fingerprint(JULY))

    def test_trailing_empty_header_cells_are_ignored(self):
        padded = [HEADER + ["", ""]] + [r + ["", ""] for r in JULY[1:]]
        self.assertEqual(layout.fingerprint(padded), (layout.fingerprint(JULY)[0], HEADER))

    def test_returns_the_raw_header_for_display(self):
        self.assertEqual(layout.fingerprint(JULY)[1], HEADER)

    def test_headerless_shape_is_stable_and_has_no_display_header(self):
        other_month = [["09/01/2024", "-12.00", "*", "", "SHELL"], ["09/04/2024", "-8.75", "*", "", "SUBWAY"]]
        self.assertEqual(layout.fingerprint(HEADERLESS), layout.fingerprint(other_month))
        self.assertIsNone(layout.fingerprint(HEADERLESS)[1])

    def test_headerless_files_with_different_widths_differ(self):
        narrower = [r[:4] for r in HEADERLESS]
        self.assertNotEqual(layout.fingerprint(narrower)[0], layout.fingerprint(HEADERLESS)[0])


if __name__ == "__main__":
    unittest.main()
