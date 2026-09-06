import unittest
from datetime import date

from importer.dates import apply_year, find_period, infer_year, parse_date

PERIOD = (date(2024, 12, 15), date(2025, 1, 14))


class ParseDateTest(unittest.TestCase):
    def test_formats(self):
        cases = {
            "08/12/2024": date(2024, 8, 12), "8/12/24": date(2024, 8, 12), "2024-08-12": date(2024, 8, 12),
            "20240812": date(2024, 8, 12), "Aug 12, 2024": date(2024, 8, 12), "12 Aug 2024": date(2024, 8, 12),
            "2024-08-12T10:00:00": date(2024, 8, 12), "August 12, 2024": date(2024, 8, 12),
            "12-Aug-2024": date(2024, 8, 12), "2024/08/12": date(2024, 8, 12),
        }
        self.assertEqual({k: parse_date(k) for k in cases}, cases)

    def test_month_day_without_year_uses_default_year(self):
        self.assertEqual([parse_date("08/12", default_year=2023), parse_date("Sep 3", default_year=2023),
                          parse_date("AUG12", default_year=2023), parse_date("3 Sep", default_year=2023)],
                         [date(2023, 8, 12), date(2023, 9, 3), date(2023, 8, 12), date(2023, 9, 3)])

    def test_day_first(self):
        self.assertEqual(parse_date("03/08/2024", day_first=True), date(2024, 8, 3))
        self.assertEqual(parse_date("13/08/2024"), date(2024, 8, 13))

    def test_explicit_format_wins(self):
        self.assertEqual(parse_date("03/08/2024", fmt="%d/%m/%Y"), date(2024, 8, 3))

    def test_junk_returns_none(self):
        self.assertEqual([parse_date(x) for x in ["junk", "12", "", None, "1,234.56", "DEBIT"]], [None] * 6)


class PeriodTest(unittest.TestCase):
    def test_numeric_range(self):
        self.assertEqual(find_period("Statement Period 12/15/2024 - 01/14/2025"), PERIOD)

    def test_text_range_and_opening_closing(self):
        self.assertEqual(find_period("Opening Date: Dec 15, 2024   Closing Date: Jan 14, 2025"), PERIOD)
        self.assertEqual(find_period("From August 1, 2024 to August 31, 2024"), (date(2024, 8, 1), date(2024, 8, 31)))

    def test_closing_date_alone(self):
        self.assertEqual(find_period("Closing Date 01/14/2025"), (date(2024, 12, 10), date(2025, 1, 14)))

    def test_none_when_absent(self):
        self.assertIsNone(find_period("No dates here"))


class InferYearTest(unittest.TestCase):
    def test_december_rows_get_start_year_and_january_rows_end_year(self):
        self.assertEqual((infer_year(12, 20, PERIOD), infer_year(1, 5, PERIOD)), (2024, 2025))

    def test_apply_year(self):
        self.assertEqual(apply_year(date(1900, 12, 20), PERIOD), date(2024, 12, 20))

    def test_without_period_future_dates_roll_back(self):
        today = date(2025, 3, 1)
        self.assertEqual((infer_year(2, 10, None, today), infer_year(11, 10, None, today)), (2025, 2024))


if __name__ == "__main__":
    unittest.main()


class LastThisStatementPeriodTest(unittest.TestCase):
    def test_last_and_this_statement_lines_give_the_period(self):
        from datetime import date
        text = "Last statement: March 08, 2026\nThis statement: April 08, 2026\nTotal days in statement period: 31"
        self.assertEqual(find_period(text), (date(2026, 3, 9), date(2026, 4, 8)))
