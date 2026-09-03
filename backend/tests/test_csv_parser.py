import os
import unittest
from datetime import date
from decimal import Decimal

from importer import csv_parser
from importer.models import Mapping

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read()


def summary(result):
    return [(r.txn_date, r.amount, r.description) for r in result.rows if r.is_valid]


# (fixture, expected profile, number of valid rows, first valid row, one row proving the sign convention)
BANK_CASES = [
    ("chase_checking.csv", "chase", 8, (date(2024, 8, 1), Decimal("-6.45"), "STARBUCKS STORE 12345 CHICAGO IL"),
     (date(2024, 8, 2), Decimal("2500.00"), "PAYROLL ACME CORP DIRECT DEP")),
    ("chase_card.csv", "chase", 6, (date(2024, 8, 1), Decimal("-42.99"), "AMZN Mktp US*2A3BC4D5E"),
     (date(2024, 8, 4), Decimal("450.00"), "Payment Thank You-Mobile")),
    ("amex.csv", "amex", 5, (date(2024, 8, 2), Decimal("-84.12"), "WHOLE FOODS MARKET CHICAGO IL"),
     (date(2024, 8, 6), Decimal("600.00"), "AUTOPAY PAYMENT - THANK YOU")),
    ("capital_one_card.csv", "capital_one", 4, (date(2024, 8, 1), Decimal("-63.20"), "TARGET 00012345 CHICAGO IL"),
     (date(2024, 8, 3), Decimal("300.00"), "CAPITAL ONE AUTOPAY PYMT")),
    ("capital_one_360.csv", "capital_one", 3, (date(2024, 8, 1), Decimal("-75.00"), "Withdrawal to Chase"),
     (date(2024, 8, 5), Decimal("1500.00"), "Deposit from ACME PAYROLL")),
    ("bofa_checking.csv", "bofa", 5, (date(2024, 8, 2), Decimal("2500.00"),
                                      "ACME CORP DES:PAYROLL ID:123456 INDN:JOHN DOE CO ID:1234567890 PPD"),
     (date(2024, 8, 5), Decimal("-92.34"), "CHECKCARD 0803 KROGER #123 CHICAGO IL 24692164215100012345678")),
    ("bofa_card.csv", "bofa", 3, (date(2024, 8, 3), Decimal("-6.45"), "STARBUCKS STORE 12345"),
     (date(2024, 8, 5), Decimal("350.00"), "PAYMENT - THANK YOU")),
    ("citi.csv", "citi", 4, (date(2024, 8, 2), Decimal("-156.78"), "COSTCO WHSE #1234 CHICAGO IL"),
     (date(2024, 8, 4), Decimal("500.00"), "AUTOPAY PAYMENT THANK YOU")),
    ("discover.csv", "discover", 3, (date(2024, 8, 1), Decimal("-88.20"), "WALMART SUPERCENTER CHICAGO IL"),
     (date(2024, 8, 5), Decimal("200.00"), "INTERNET PAYMENT - THANK YOU")),
    ("wells_fargo.csv", "wells_fargo", 5, (date(2024, 8, 1), Decimal("-45.20"),
                                           "PURCHASE AUTHORIZED ON 07/31 TRADER JOE'S #123 CHICAGO IL S1234567890 CARD 1234"),
     (date(2024, 8, 3), Decimal("2500.00"), "ACME CORP PAYROLL 240803 JOHN DOE")),
    ("rbc.csv", "rbc", 5, (date(2024, 8, 1), Decimal("-4.25"), "TIM HORTONS #4412 TORONTO ON"),
     (date(2024, 8, 2), Decimal("3200.00"), "PAYROLL DEPOSIT ACME LTD")),
    ("td.csv", "td", 4, (date(2024, 8, 1), Decimal("-4.25"), "TIM HORTONS #4412"),
     (date(2024, 8, 2), Decimal("3200.00"), "PAYROLL DEPOSIT ACME")),
    ("bmo_chequing.csv", "bmo", 3, (date(2024, 8, 1), Decimal("-52.30"), "SHOPPERS DRUG MART #123 TORONTO ON"),
     (date(2024, 8, 2), Decimal("3100.00"), "PAYROLL ACME LTD")),
    ("bmo_card.csv", "bmo", 3, (date(2024, 8, 1), Decimal("-45.60"), "CANADIAN TIRE #123 TORONTO ON"),
     (date(2024, 8, 3), Decimal("500.00"), "PAYMENT RECEIVED - THANK YOU")),
]


class BankFixturesTest(unittest.TestCase):
    def test_each_bank_fixture_parses_with_normalised_signs(self):
        for name, profile, n, first, proof in BANK_CASES:
            with self.subTest(name):
                result = csv_parser.parse_csv(load(name))
                rows = summary(result)
                self.assertEqual((result.profile, len(rows), rows[0]), (profile, n, first))
                self.assertIn(proof, rows)
                self.assertGreaterEqual(result.profile_confidence, 0.8)

    def test_bofa_preamble_is_skipped_and_balance_row_invalid(self):
        result = csv_parser.parse_csv(load("bofa_checking.csv"))
        invalid = [r for r in result.rows if not r.is_valid]
        self.assertEqual((result.mapping.skip_rows, result.header, len(invalid), invalid[0].problems),
                         (5, ["Date", "Description", "Amount", "Running Bal."], 1, ["No amount"]))

    def test_citi_negative_credits_become_positive(self):
        rows = summary(csv_parser.parse_csv(load("citi.csv")))
        self.assertEqual([r[1] for r in rows], [Decimal("-156.78"), Decimal("500.00"), Decimal("-9.99"), Decimal("20.00")])

    def test_rbc_joins_two_description_columns_and_reads_cad_column(self):
        result = csv_parser.parse_csv(load("rbc.csv"))
        self.assertEqual((result.mapping.description, result.mapping.currency_columns), ([4, 5], {"CAD": 6, "USD": 7}))

    def test_scotiabank_shape_detects_as_wells_fargo_but_can_be_forced(self):
        auto = csv_parser.parse_csv(load("scotiabank.csv"))
        forced = csv_parser.parse_csv(load("scotiabank.csv"), profile_key="scotiabank")
        self.assertEqual((auto.profile, forced.profile, summary(auto)), ("wells_fargo", "scotiabank", summary(forced)))

    def test_period_is_min_max_of_valid_dates(self):
        result = csv_parser.parse_csv(load("chase_checking.csv"))
        self.assertEqual(result.period, (date(2024, 8, 1), date(2024, 8, 12)))


class GenericTest(unittest.TestCase):
    def test_headerless_generic(self):
        result = csv_parser.parse_csv(load("generic_headerless.csv"))
        self.assertEqual((result.profile, summary(result)), (None, [
            (date(2024, 8, 1), Decimal("-4.50"), "Coffee shop"),
            (date(2024, 8, 2), Decimal("3000.00"), "Salary"),
            (date(2024, 8, 3), Decimal("-120.00"), "Grocery store"),
        ]))
        self.assertTrue(any("not recognised" in w for w in result.warnings))

    def test_semicolon_day_first_debit_credit(self):
        result = csv_parser.parse_csv(load("generic_semicolon.csv"))
        self.assertEqual((result.mapping.delimiter, result.mapping.day_first, result.mapping.sign,
                          summary(result)[0]), (";", True, "debit_credit", (date(2024, 8, 13), Decimal("-4.50"), "COFFEE SHOP")))

    def test_mostly_positive_amounts_warn_about_sign(self):
        result = csv_parser.parse_csv(load("generic_positive_charges.csv"))
        self.assertIsNone(result.profile)
        self.assertTrue(any("flip the sign" in w for w in result.warnings))

    def test_user_mapping_overrides_detection(self):
        mapping = Mapping(has_header=True, date=0, description=[1], amount=2, flip_sign=True)
        result = csv_parser.parse_csv(load("generic_positive_charges.csv"), mapping=mapping)
        self.assertEqual(summary(result)[0], (date(2024, 8, 1), Decimal("-4.50"), "COFFEE SHOP"))
        self.assertEqual(summary(result)[-1][1], Decimal("200.00"))

    def test_bom_and_blank_lines(self):
        text = "﻿Date,Description,Amount\n\n08/01/2024,Coffee,-4.50\n\n"
        result = csv_parser.parse_csv(text)
        self.assertEqual(summary(result), [(date(2024, 8, 1), Decimal("-4.50"), "Coffee")])

    def test_unparseable_date_is_reported_not_dropped(self):
        text = "Date,Description,Amount\n13/45/2024,Broken,-1.00\n08/01/2024,Fine,-2.00\n"
        result = csv_parser.parse_csv(text)
        bad = [r for r in result.rows if not r.is_valid]
        self.assertEqual((len(result.rows), bad[0].problems), (2, ["Unparseable date '13/45/2024'"]))


class DelimiterTest(unittest.TestCase):
    def test_detects_tab_and_pipe(self):
        self.assertEqual((csv_parser.detect_delimiter("a\tb\tc\n1\t2\t3\n"), csv_parser.detect_delimiter("a|b\n1|2\n")),
                         ("\t", "|"))


if __name__ == "__main__":
    unittest.main()
