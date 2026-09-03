import os
import unittest
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

import transfers  # noqa: E402


class ScoreTest(unittest.TestCase):
    def test_hint_on_either_side_raises_confidence(self):
        self.assertEqual(transfers.score("PAYMENT THANK YOU", "AMEX EPAYMENT"), 0.9)
        self.assertEqual(transfers.score("CHASE CREDIT CRD AUTOPAY", "ONLINE PMT"), 0.9)
        self.assertEqual(transfers.score("INTERAC E-TRANSFER SENT", "DEPOSIT"), 0.9)
        self.assertEqual(transfers.score("CHECK 1234", "DEPOSIT"), 0.6)


class AutoPairTest(unittest.TestCase):
    def test_pairs_only_confident_and_unused(self):
        cands = [
            {"a": {"id": 1, "txn_date": "2026-09-01", "description": "PAYMENT THANK YOU"}, "b": {"id": 10, "description": "X"}, "confidence": 0.9},
            {"a": {"id": 1, "txn_date": "2026-09-01", "description": "PAYMENT THANK YOU"}, "b": {"id": 11, "description": "X"}, "confidence": 0.9},
            {"a": {"id": 2, "txn_date": "2026-09-01", "description": "CHECK"}, "b": {"id": 12, "description": "X"}, "confidence": 0.6},
            {"a": {"id": 3, "txn_date": "2026-09-01", "description": "TRANSFER"}, "b": {"id": 13, "description": "X"}, "confidence": 0.9},
        ]
        paired = []
        with mock.patch.object(transfers, "candidates", return_value=cands), \
             mock.patch.object(transfers, "pair", side_effect=lambda uid, a, b: paired.append((a, b))):
            self.assertEqual(transfers.auto_pair(1), 2)
        self.assertEqual(paired, [(1, 10), (3, 13)])

    def test_pair_errors_are_skipped(self):
        cands = [{"a": {"id": 1, "txn_date": "d", "description": "TRANSFER"}, "b": {"id": 2, "description": ""}, "confidence": 0.9}]
        with mock.patch.object(transfers, "candidates", return_value=cands), \
             mock.patch.object(transfers, "pair", side_effect=ValueError("nope")):
            self.assertEqual(transfers.auto_pair(1), 0)


if __name__ == "__main__":
    unittest.main()
