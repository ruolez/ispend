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


class PairGuardsTest(unittest.TestCase):
    def _rows(self, a_pair=None, b_pair=None):
        return [{"id": 1, "account_id": 10, "amount": -50, "transfer_pair_id": a_pair, "account_type": "checking"},
                {"id": 2, "account_id": 11, "amount": 50, "transfer_pair_id": b_pair, "account_type": "credit_card"}]

    def test_a_leg_paired_elsewhere_is_refused(self):
        with mock.patch.object(transfers.db, "query", return_value=self._rows(b_pair=99)):
            with self.assertRaises(transfers.AlreadyPaired):
                transfers.pair(1, 1, 2)

    def test_repairing_the_same_two_legs_is_allowed(self):
        calls = []
        with mock.patch.object(transfers.db, "query", side_effect=[self._rows(a_pair=2, b_pair=1), {"id": 7}]), \
             mock.patch.object(transfers.db, "execute", side_effect=lambda *a, **k: calls.append(a)), \
             mock.patch.object(transfers.db, "transaction", create=True) as tx, \
             mock.patch.object(transfers, "record_events"):
            tx.return_value.__enter__ = lambda *a: None
            tx.return_value.__exit__ = lambda *a: False
            out = transfers.pair(1, 1, 2)
        self.assertEqual((out["a_id"], out["b_id"], len(calls)), (1, 2, 2))
