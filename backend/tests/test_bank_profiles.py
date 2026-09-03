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
                                "bmo", "scotiabank"})
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
        }
        self.assertEqual({t: bank_profiles.detect_pdf(t)[0].key for t in cases}, cases)

    def test_unknown_text(self):
        self.assertEqual(bank_profiles.detect_pdf("Some Credit Union statement"), (None, 0.0))


if __name__ == "__main__":
    unittest.main()
