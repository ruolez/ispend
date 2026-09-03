import unittest

from merchant import normalize

CASES = [
    ("SQ *BLUE BOTTLE COFFEE CHICAGO IL", "BLUE BOTTLE COFFEE", "Blue Bottle Coffee"),
    ("TST* THE BURGER JOINT 123 EVANSTON IL", "THE BURGER JOINT", "The Burger Joint"),
    ("AMZN Mktp US*2A3BC4D5E Amzn.com/bill WA", "AMAZON", "Amazon"),
    ("PAYPAL *NETFLIX.COM 402-935-7733 CA", "NETFLIX", "Netflix"),
    ("STARBUCKS STORE 12345 CHICAGO IL", "STARBUCKS", "Starbucks"),
    ("THE HOME DEPOT #1234 NORTH CHICAGO IL", "HOME DEPOT", "Home Depot"),
    ("POS DEBIT CARD 1234 WALMART SUPERCENTER 08/12", "WALMART", "Walmart"),
    ("UBER *EATS PENDING SAN FRANCISCO CA", "UBER EATS", "Uber Eats"),
    ("TIM HORTONS #4412 TORONTO ON", "TIM HORTONS", "Tim Hortons"),
    ("COSTCO WHSE #1234 CALGARY AB", "COSTCO", "Costco"),
    ("WHOLEFDS LKV 10231 CHICAGO IL", "WHOLE FOODS", "Whole Foods"),
    ("MCDONALD'S F12345 CHICAGO IL", "MCDONALD'S", "McDonald's"),
    ("APPLE.COM/BILL 866-712-7753 CA", "APPLE", "Apple"),
    ("ACH DEBIT COMED 1234567890", "COMED", "Comed"),
    ("ATM WITHDRAWAL 00012345 CHICAGO IL", "ATM", "ATM"),
    ("CHECKCARD 0803 KROGER #123 CHICAGO IL 24692164215100012345678", "KROGER", "Kroger"),
    ("Spotify USA", "SPOTIFY", "Spotify"),
    ("netflix.com", "NETFLIX", "Netflix"),
]


class NormalizeTest(unittest.TestCase):
    def test_keys_and_names(self):
        got = [(raw, normalize(raw).key, normalize(raw).name) for raw, _, _ in CASES]
        self.assertEqual(got, CASES)

    def test_idempotent(self):
        for raw, _, _ in CASES:
            m = normalize(raw)
            self.assertEqual(normalize(m.clean).key, m.key, raw)

    def test_same_merchant_different_store_and_city_share_key(self):
        a = normalize("STARBUCKS STORE 12345 CHICAGO IL")
        b = normalize("STARBUCKS STORE 98765 EVANSTON IL")
        self.assertEqual(a.key, b.key)

    def test_empty_and_numeric_descriptions(self):
        self.assertEqual(normalize("").key, "UNKNOWN")
        self.assertEqual(normalize("1042").clean, "1042")

    def test_last4_and_store_numbers_removed(self):
        m = normalize("PURCHASE AUTHORIZED ON 07/31 TRADER JOE'S #123 CHICAGO IL S1234567890 CARD 1234")
        self.assertEqual((m.key, m.name), ("TRADER JOE'S", "Trader Joe's"))


if __name__ == "__main__":
    unittest.main()


class PaymentDescriptionsTest(unittest.TestCase):
    """Descriptions made only of 'noise' words must keep their meaning instead of collapsing."""

    def test_card_payments_alias_to_card_payment(self):
        for raw in ["Payment Thank You-Mobile", "AUTOMATIC PAYMENT - THANK", "ONLINE PAYMENT THANK YOU",
                    "AUTOPAY PAYMENT - THANK YOU"]:
            with self.subTest(raw=raw):
                m = merchant.normalize(raw)
                self.assertEqual((m.key, m.name), ("CARD PAYMENT", "Card Payment"))

    def test_etransfer_keeps_counterparty(self):
        m = merchant.normalize("INTERAC E-TRANSFER SENT")
        self.assertEqual(m.name, "Interac e-Transfer")
        self.assertEqual(merchant.normalize("INTERAC E-TRANSFER RECEIVED JOHN SMITH").key, "JOHN SMITH")
