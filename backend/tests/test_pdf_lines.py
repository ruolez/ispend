import json
import os
import unittest
from datetime import date
from decimal import Decimal

import bank_profiles
from importer import pdf_lines
from importer.dates import find_period

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def run(name, profile_key=None, flip_sign=False):
    with open(os.path.join(FIX, name)) as f:
        words = json.load(f)
    lines = pdf_lines.cluster_rows(words)
    text = pdf_lines.lines_text(lines)
    profile, _conf = bank_profiles.detect_pdf(text)
    if profile_key:
        profile = bank_profiles.get(profile_key)
    period = find_period(text)
    rows = pdf_lines.rows_to_transactions(lines, profile, period, flip_sign=flip_sign)
    return profile, period, [(r.txn_date, r.amount, r.description, r.balance) for r in rows]


class ClusterRowsTest(unittest.TestCase):
    def test_words_group_by_top_and_sort_by_x(self):
        words = [{"text": "b", "x0": 50, "x1": 56, "top": 100.5, "bottom": 110},
                 {"text": "a", "x0": 10, "x1": 16, "top": 101.2, "bottom": 111},
                 {"text": "c", "x0": 10, "x1": 16, "top": 120, "bottom": 130}]
        lines = pdf_lines.cluster_rows(words)
        self.assertEqual([(ln.text, ln.x0) for ln in lines], [("a b", 10.0), ("c", 10.0)])


class ChaseCardTest(unittest.TestCase):
    def test_sections_sign_multiline_year_and_stop(self):
        profile, period, rows = run("words_chase_card.json")
        self.assertEqual((profile.key, period), ("chase", (date(2024, 7, 15), date(2024, 8, 14))))
        self.assertEqual(rows, [
            (date(2024, 7, 20), Decimal("450.00"), "Payment Thank You-Mobile", None),
            (date(2024, 7, 22), Decimal("-42.99"), "AMZN Mktp US*2A3BC4D5E Amzn.com/bill WA", None),
            (date(2024, 7, 25), Decimal("-5.75"), "SQ *BLUE BOTTLE COFFEE CHICAGO IL", None),
            (date(2024, 7, 28), Decimal("-28.40"), "UBER *EATS SAN FRANCISCO CA help.uber.com CA", None),
            (date(2024, 8, 10), Decimal("-15.49"), "NETFLIX.COM LOS GATOS CA", None),
        ])


class AmexTest(unittest.TestCase):
    def test_dollar_amounts_and_credit_section(self):
        profile, _period, rows = run("words_amex.json")
        self.assertEqual(profile.key, "amex")
        self.assertEqual(rows, [
            (date(2024, 8, 2), Decimal("-84.12"), "WHOLE FOODS MARKET CHICAGO IL", None),
            (date(2024, 8, 9), Decimal("-10.99"), "SPOTIFY USA NEW YORK NY", None),
            (date(2024, 8, 6), Decimal("600.00"), "AUTOPAY PAYMENT - THANK YOU", None),
        ])


class RbcTest(unittest.TestCase):
    def test_withdrawal_deposit_columns_inferred_from_x_position(self):
        profile, period, rows = run("words_rbc.json")
        self.assertEqual((profile.key, period), ("rbc", (date(2024, 8, 1), date(2024, 8, 31))))
        self.assertEqual(rows, [
            (date(2024, 8, 1), Decimal("-4.25"), "TIM HORTONS #4412", Decimal("995.75")),
            (date(2024, 8, 2), Decimal("3200.00"), "PAYROLL DEPOSIT ACME LTD", Decimal("4195.75")),
            (date(2024, 8, 5), Decimal("-96.40"), "LOBLAWS #1234", Decimal("4099.35")),
        ])


class GenericTest(unittest.TestCase):
    def test_signed_amounts_with_balance(self):
        profile, period, rows = run("words_generic.json")
        self.assertEqual((profile, period), (None, (date(2024, 8, 1), date(2024, 8, 31))))
        self.assertEqual(rows, [
            (date(2024, 8, 2), Decimal("-4.50"), "COFFEE SHOP", Decimal("995.50")),
            (date(2024, 8, 3), Decimal("3000.00"), "SALARY DEPOSIT", Decimal("3995.50")),
            (date(2024, 8, 5), Decimal("-120.00"), "GROCERY STORE", Decimal("3875.50")),
        ])

    def test_flip_sign_override(self):
        _p, _period, rows = run("words_generic.json", flip_sign=True)
        self.assertEqual([r[1] for r in rows], [Decimal("4.50"), Decimal("-3000.00"), Decimal("120.00")])

    def test_year_inference_across_december(self):
        lines = [pdf_lines.Line(text=t, top=i * 12, x0=30, x1=500, page=1, words=[]) for i, t in enumerate([
            "12/28 HOLIDAY STORE 20.00", "01/03 GROCERY 30.00"])]
        rows = pdf_lines.rows_to_transactions(lines, None, (date(2024, 12, 15), date(2025, 1, 14)))
        self.assertEqual([r.txn_date for r in rows], [date(2024, 12, 28), date(2025, 1, 3)])


if __name__ == "__main__":
    unittest.main()


class OcrRepairTest(unittest.TestCase):
    """OCR splits amounts at the decimal point and detaches minus signs; repair must fix
    exactly those shapes and leave well-formed lines alone."""

    CASES = [
        ("07/22/24 STARBUCKS STORE 05412 CHICAGO IL $6 45", "07/22/24 STARBUCKS STORE 05412 CHICAGO IL $6.45"),
        ("07/25/24 WHOLEFDS LKV 10259 EVANSTON IL $84. 12", "07/25/24 WHOLEFDS LKV 10259 EVANSTON IL $84.12"),
        ("08/12/24 DELTA AIR LINES ATLANTA GA $412 - 60", "08/12/24 DELTA AIR LINES ATLANTA GA $412.60"),
        ("08/01/24 AUTOPAY PAYMENT - THANK YOU - $500. 00", "08/01/24 AUTOPAY PAYMENT - THANK YOU -$500.00"),
        ("08/01/24 PAYMENT − $500.00", "08/01/24 PAYMENT -$500.00"),
        ("Total New Charges $626 . 53", "Total New Charges $626.53"),
    ]

    def test_repairs_split_amounts(self):
        for raw, fixed in self.CASES:
            with self.subTest(raw=raw):
                self.assertEqual(pdf_lines.ocr_repair(raw), fixed)

    def test_leaves_well_formed_and_amountless_lines_alone(self):
        for line in ["01/02/2024 NORMAL LINE 1,234.56", "07/22/24 STARBUCKS STORE 05412",
                     "08/09/24 AMAZON .COM*2K4J75 AMZN.COM/BILL WA $31 wl", "12/01/24 SOMETHING (12.34)"]:
            with self.subTest(line=line):
                self.assertEqual(pdf_lines.ocr_repair(line), line)


class UnreadableLineTest(unittest.TestCase):
    def test_date_led_line_without_amount_becomes_invalid_row(self):
        from datetime import date
        lines = [
            pdf_lines.Line(text="08/05/24 SHELL OIL 57442 SKOKIE IL $52.10", top=10.0, x0=72.0, x1=400.0, words=[], page=1),
            pdf_lines.Line(text="08/09/24 AMAZON .COM*2K4J75 AMZN.COM/BILL WA $31 wl", top=24.0, x0=72.0, x1=400.0, words=[], page=1),
        ]
        rows = pdf_lines.rows_to_transactions(lines, None, (date(2024, 7, 21), date(2024, 8, 20)))
        self.assertEqual([(r.txn_date, r.amount, r.is_valid, r.problems) for r in rows], [
            (date(2024, 8, 5), rows[0].amount, True, []),
            (date(2024, 8, 9), None, False, ["Could not read an amount on this line"]),
        ])


class DashDateBankLayoutTest(unittest.TestCase):
    """Byline-style checking statements: MM-DD dates, a marker glyph, amount + running balance,
    indented detail lines, and a 'Beginning balance' row that is not a transaction."""

    def _line(self, text, top, x0):
        return pdf_lines.Line(text=text, top=top, x0=x0, x1=500.0, words=[], page=1)

    def test_rows_with_balance_and_continuations(self):
        from datetime import date
        L = self._line
        lines = [
            L("Date Description Additions Subtractions Balance", 10, 109.3),
            L("03-08 Beginning balance $1,240.61", 20, 109.3),
            L("03-10 ' Funds Transfer 45.00 1,285.61", 30, 109.3),
            L("FROM ACCT *9885 FUNDS TRANSFER VIA M", 40, 157.6),
            L("03-17 ' ACH Withdrawal -10.00 1,327.61", 50, 109.3),
            L("PAYPAL INST XFER", 60, 157.6),
            L("03-23 ' A2A Payment Credit 515.00 1,972.61", 70, 109.3),
            L("ZELLE EXQUISITE JEWELERS", 80, 157.6),
        ]
        rows = pdf_lines.rows_to_transactions(lines, None, (date(2026, 3, 9), date(2026, 4, 8)))
        self.assertEqual([(r.txn_date, str(r.amount), r.description, str(r.balance)) for r in rows], [
            (date(2026, 3, 10), "45.00", "Funds Transfer FROM ACCT *9885 FUNDS TRANSFER VIA M", "1285.61"),
            (date(2026, 3, 17), "-10.00", "ACH Withdrawal PAYPAL INST XFER", "1327.61"),
            (date(2026, 3, 23), "515.00", "A2A Payment Credit ZELLE EXQUISITE JEWELERS", "1972.61"),
        ])


class PageHeaderTest(unittest.TestCase):
    def test_page_headers_are_not_damaged_rows(self):
        from datetime import date
        L = lambda t, top: pdf_lines.Line(text=t, top=top, x0=109.3, x1=500.0, words=[], page=2)
        lines = [L("May 08, 2026 0000422304", 10), L("05-01 ' A2A Payment Credit 40.00 482.53", 20),
                 L("08/09/24 AMAZON .COM*2K4J75 AMZN.COM/BILL WA $31 wl", 30)]
        rows = pdf_lines.rows_to_transactions(lines, None, (date(2026, 4, 9), date(2026, 5, 8)))
        self.assertEqual([(str(r.amount), r.is_valid, r.description[:12]) for r in rows],
                         [("40.00", True, "A2A Payment "), ("None", False, "AMAZON .COM*")])


class DepositTicketPageTest(unittest.TestCase):
    """A deposit-ticket listing on a later page repeats ledger deposits without a balance and with
    amounts printed further left; those rows are twins, not withdrawals."""

    def test_twins_flagged_and_signs_judged_per_page(self):
        from datetime import date
        W = lambda t, x0: {"text": t, "x0": x0, "x1": x0 + 30, "top": 0, "bottom": 10}
        def L(text, top, x0, page, amt_x0):
            return pdf_lines.Line(text=text, top=top, x0=x0, x1=500.0, words=[W(w, x0 + i * 60) for i, w in enumerate(text.split())][:-1] + [W(text.split()[-1], amt_x0)], page=page)
        lines = [
            L("06-30 Deposit 9,000.00 15,346.29", 10, 109, 3, 400),
            L("07-01 Withdrawal 800.00 14,546.29", 20, 109, 3, 400),
            L("07-06 Deposit 5,000.00 19,626.29", 30, 109, 3, 400),
            L("06/30/2026 Deposit $9,000.00", 10, 72, 4, 200),
            L("07/06/2026 Deposit $5,000.00", 20, 72, 4, 200),
        ]
        rows = pdf_lines.rows_to_transactions(lines, None, (date(2026, 6, 9), date(2026, 7, 8)))
        got = [(r.txn_date, str(r.amount), r.balance is not None, r.raw.get("twin_of") is not None) for r in rows]
        self.assertEqual(got, [
            (date(2026, 6, 30), "9000.00", True, False),
            (date(2026, 7, 1), "800.00", True, False),
            (date(2026, 7, 6), "5000.00", True, False),
            (date(2026, 6, 30), "9000.00", False, True),
            (date(2026, 7, 6), "5000.00", False, True),
        ])
