import unittest

import builtin_hints
import seed_categories


class LookupTest(unittest.TestCase):
    def test_brands(self):
        cases = [
            ("STARBUCKS", "", "dining.coffee"),
            ("TIM HORTONS", "TIM HORTONS #1234 TORONTO ON", "dining.coffee"),
            ("BLUE BOTTLE COFFEE", "", "dining.coffee"),
            ("LOBLAWS", "", "groceries"),
            ("PETRO-CANADA", "PETRO-CANADA 09876", "transport.fuel"),
            ("NETFLIX.COM", "", "subscriptions.streaming"),
            ("PAYMENT THANK YOU", "", "transfers.card_payment"),
            ("INTERAC E-TRANSFER", "INTERAC E-TRANSFER SENT", "transfers.internal"),
            ("PAYROLL", "ACME CORP PAYROLL", "income.salary"),
            ("ATM WITHDRAWAL", "", "cash"),
            ("HYDRO ONE", "", "utilities.electricity"),
            ("AMZN MKTP", "AMZN MKTP CA*2K3", "shopping.online"),
        ]
        for key, clean, slug in cases:
            with self.subTest(key=key):
                self.assertEqual(builtin_hints.lookup(key, clean), slug)

    def test_longest_keyword_wins(self):
        self.assertEqual(builtin_hints.lookup("UBER EATS", ""), "dining.delivery")
        self.assertEqual(builtin_hints.lookup("UBER TRIP", ""), "transport.rideshare")
        self.assertEqual(builtin_hints.lookup("AMAZON PRIME", ""), "subscriptions.memberships")

    def test_word_boundaries(self):
        self.assertIsNone(builtin_hints.lookup("SEASHELL GIFTS", ""))
        self.assertIsNone(builtin_hints.lookup("ATMOSPHERE", ""))
        self.assertEqual(builtin_hints.lookup("SHELL", "SHELL OIL 57444"), "transport.fuel")

    def test_unknown_returns_none(self):
        self.assertIsNone(builtin_hints.lookup("ZQX UNKNOWN VENDOR", "ZQX UNKNOWN VENDOR 1"))
        self.assertIsNone(builtin_hints.lookup("", ""))

    def test_every_slug_exists_in_default_taxonomy(self):
        slugs = set()
        for slug, _n, _k, _c, _i, children in seed_categories.DEFAULT_TAXONOMY:
            slugs.add(slug)
            slugs.update(c[0] for c in children)
        self.assertEqual(sorted(set(builtin_hints.all_slugs()) - slugs), [])


if __name__ == "__main__":
    unittest.main()
