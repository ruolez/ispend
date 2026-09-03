import unittest

from importer import generic


class HeaderRowTest(unittest.TestCase):
    def test_finds_header_after_preamble(self):
        rows = [["Summary"], ["Beginning balance", "1,000.00"], ["Date", "Description", "Amount", "Running Bal."]]
        self.assertEqual(generic.find_header_row(rows), 2)

    def test_none_for_headerless(self):
        rows = [["08/01/2024", "-45.20", "*", "", "TRADER JOE'S"]]
        self.assertIsNone(generic.find_header_row(rows))


class GuessMappingTest(unittest.TestCase):
    def test_header_hints_prefer_transaction_date_over_post_date(self):
        rows = [["Post Date", "Transaction Date", "Description", "Amount"],
                ["08/02/2024", "08/01/2024", "COFFEE", "-4.50"]]
        m, _ = generic.guess_mapping(rows)
        self.assertEqual((m.date, m.posted_date, m.description, m.amount, m.sign), (1, 0, [2], 3, "as_is"))

    def test_withdrawal_deposit_headers_map_to_debit_credit(self):
        rows = [["Date", "Payee", "Withdrawal", "Deposit", "Balance"], ["08/01/2024", "X", "4.50", "", "10.00"]]
        m, _ = generic.guess_mapping(rows)
        self.assertEqual((m.debit, m.credit, m.balance, m.sign), (2, 3, 4, "debit_credit"))

    def test_headerless_wells_fargo_shape(self):
        rows = [["08/01/2024", "-45.20", "*", "", "TRADER JOE'S"], ["08/03/2024", "2500.00", "*", "", "PAYROLL"]]
        m, _ = generic.guess_mapping(rows)
        self.assertEqual((m.has_header, m.date, m.amount, m.description), (False, 0, 1, [4]))

    def test_headerless_three_money_columns_are_debit_credit_balance(self):
        rows = [["08/01/2024", "TIM HORTONS", "4.25", "", "1500.00"], ["08/02/2024", "PAYROLL", "", "3200.00", "4700.00"],
                ["08/05/2024", "SOBEYS", "96.40", "", "4603.60"]]
        m, _ = generic.guess_mapping(rows)
        self.assertEqual((m.debit, m.credit, m.balance, m.description, m.sign), (2, 3, 4, [1], "debit_credit"))

    def test_type_column_drives_sign(self):
        rows = [["Date", "Amount", "Type", "Description"], ["08/01/2024", "75.00", "Debit", "X"], ["08/02/2024", "10.00", "Credit", "Y"]]
        m, _ = generic.guess_mapping(rows)
        self.assertEqual((m.type_col, m.debit_types), (2, ["debit", "dr"]))

    def test_day_first_elected_when_day_exceeds_twelve(self):
        rows = [["Date", "Description", "Amount"], ["13/08/2024", "A", "-1.00"], ["14/08/2024", "B", "-2.00"]]
        m, _ = generic.guess_mapping(rows)
        self.assertEqual((m.date_format, m.day_first), ("%d/%m/%Y", True))

    def test_ambiguous_dates_default_month_first(self):
        rows = [["Date", "Description", "Amount"], ["03/08/2024", "A", "-1.00"]]
        m, _ = generic.guess_mapping(rows)
        self.assertEqual((m.date_format, m.day_first), ("%m/%d/%Y", False))

    def test_positive_amounts_warn(self):
        rows = [["Date", "Description", "Amount"]] + [[f"08/0{i}/2024", "X", "5.00"] for i in range(1, 8)]
        _, warnings = generic.guess_mapping(rows)
        self.assertTrue(any("flip" in w for w in warnings))


class ElectDateFormatTest(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(generic.elect_date_format(["2024-08-01", "2024-08-02"]), ("%Y-%m-%d", False))

    def test_unknown(self):
        self.assertEqual(generic.elect_date_format(["yesterday"]), (None, False))


if __name__ == "__main__":
    unittest.main()
