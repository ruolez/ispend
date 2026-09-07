import os
import unittest

import bank_profiles
from importer.csv_parser import read_table

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
EXPECTED = {
    "chase_checking.csv": "chase", "chase_card.csv": "chase", "amex.csv": "amex", "capital_one_card.csv": "capital_one",
    "capital_one_360.csv": "capital_one", "bofa_checking.csv": "bofa", "bofa_card.csv": "bofa", "citi.csv": "citi",
    "discover.csv": "discover", "wells_fargo.csv": "wells_fargo", "rbc.csv": "rbc", "td.csv": "td",
    "bmo_chequing.csv": "bmo", "bmo_card.csv": "bmo", "scotiabank.csv": "wells_fargo",
    "pnc_checking.csv": "pnc", "pnc_classic.csv": "pnc", "pnc_activity.csv": "pnc",
    "generic_headerless.csv": None, "generic_semicolon.csv": None, "generic_positive_charges.csv": None,
}


def rows_for(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return read_table(f.read())[0]


class DetectCsvTest(unittest.TestCase):
    def test_every_fixture_detects_expected_profile(self):
        got = {}
        for name, want in EXPECTED.items():
            profile, conf, _idx, _fmt = bank_profiles.detect_csv(rows_for(name))
            got[name] = profile.key if profile else None
            if want:
                self.assertGreaterEqual(conf, 0.8, name)
        self.assertEqual(got, EXPECTED)

    def test_registry_has_all_banks_and_labels_sorted(self):
        keys = {p.key for p in bank_profiles.all_profiles()}
        self.assertEqual(keys, {"chase", "amex", "capital_one", "bofa", "citi", "discover", "wells_fargo", "rbc", "td",
                                "bmo", "scotiabank", "pnc"})
        labels = [p.label for p in bank_profiles.all_profiles()]
        self.assertEqual(labels, sorted(labels))


class DetectPdfTest(unittest.TestCase):
    def test_markers(self):
        cases = {
            "JPMorgan Chase Bank, N.A. Visit chase.com": "chase",
            "American Express  Membership Rewards summary": "amex",
            "Capital One  capitalone.com": "capital_one",
            "Bank of America, N.A.": "bofa",
            "Citibank Client Services": "citi",
            "Discover Card  Cashback Bonus": "discover",
            "Wells Fargo Everyday Checking": "wells_fargo",
            "RBC Royal Bank  Your account statement": "rbc",
            "TD Canada Trust": "td",
            "Bank of Montreal BMO": "bmo",
            "Scotiabank  scotiabank.com": "scotiabank",
            "Virtual Wallet Spend Statement PNC Bank": "pnc",
        }
        self.assertEqual({t: bank_profiles.detect_pdf(t)[0].key for t in cases}, cases)

    def test_unknown_text(self):
        self.assertEqual(bank_profiles.detect_pdf("Some Credit Union statement"), (None, 0.0))


if __name__ == "__main__":
    unittest.main()


class PdfStopTest(unittest.TestCase):
    """Every profile's pdf_stop ends the table before summary/footer lines get parsed as rows."""

    SAMPLES = {
        "bofa": ("08/05/24 SHELL OIL -52.10", "Total withdrawals and other subtractions -1,234.00"),
        "citi": ("08/05 SHELL OIL $52.10", "Fees charged $0.00"),
        "capital_one": ("Aug 5 Aug 6 SHELL OIL $52.10", "Total Transactions for This Period $852.10"),
        "discover": ("08/05/24 SHELL OIL $52.10", "Year-to-date totals"),
        "wells_fargo": ("8/5 SHELL OIL 52.10 1,000.00", "Totals $852.10 $1,000.00"),
        "rbc": ("5 Aug SHELL OIL 52.10 1,000.00", "Closing Balance 1,000.00"),
        "td": ("AUG5 SHELL OIL 52.10 1,000.00", "Total withdrawals 852.10"),
        "bmo": ("Aug 5 SHELL OIL 52.10", "Closing totals 852.10"),
        "scotiabank": ("Aug 5 SHELL OIL 52.10", "Closing Balance 1,000.00"),
        "chase": ("08/05 SHELL OIL 52.10", "Year-to-date totals"),
        "amex": ("08/05/24 SHELL OIL $52.10", "Total Fees in 2024 $0.00"),
        "pnc": ("08/05 52.10 SHELL OIL", "Daily Balance Detail"),
    }

    def test_every_profile_defines_pdf_stop_and_it_ends_the_table(self):
        from datetime import date
        from importer import pdf_lines

        period = (date(2024, 7, 21), date(2024, 8, 20))
        for key, (txn, stop) in self.SAMPLES.items():
            with self.subTest(profile=key):
                profile = bank_profiles.get(key)
                self.assertIsNotNone(profile.pdf_stop, f"{key} has no pdf_stop")
                self.assertTrue(profile.pdf_stop.match(stop), f"{key} stop regex misses {stop!r}")
                lines = [pdf_lines.Line(text=t, top=i * 12.0, x0=40.0, x1=500.0, page=1, words=[])
                         for i, t in enumerate([txn, stop, txn])]
                rows = pdf_lines.rows_to_transactions(lines, profile, period)
                self.assertEqual([(r.description, r.problems) for r in rows], [("SHELL OIL", [])])

    def test_pdf_stop_is_case_insensitive(self):
        self.assertTrue(bank_profiles.get("rbc").pdf_stop.match("CLOSING BALANCE 1,000.00"))
        self.assertTrue(bank_profiles.get("discover").pdf_stop.match("year to date totals"))
