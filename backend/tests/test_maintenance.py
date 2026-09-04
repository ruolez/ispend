import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

import maintenance  # noqa: E402



def txn(i, raw, key, name, clean, account_id=4):
    return {"id": i, "account_id": account_id, "txn_date": date(2026, 6, 1 + i % 20), "amount": Decimal("-10.00"),
            "description_raw": raw, "description_clean": clean, "merchant_key": key, "merchant_name": name,
            "fingerprint": f"old{i}", "occurrence": 1}


class RenormalizeUserTest(unittest.TestCase):
    def setUp(self):
        rows = []
        for i in range(10):
            rows.append(txn(i, "Geico Eugene Braverman GEICO AUTO 800-841-3000", "GEICO EUGENE BRAVERMAN",
                            "Geico Eugene Braverman", "GEICO EUGENE BRAVERMAN GEICO AUTO"))
        for i in range(10, 20):
            rows.append(txn(i, "Chewy Eugene Braverman CHEWY.COM", "CHEWY EUGENE BRAVERMAN", "Chewy Eugene Braverman",
                            "CHEWY EUGENE BRAVERMAN CHEWY"))
        rows.append(txn(20, "STARBUCKS STORE 05412 CHICAGO IL", "STARBUCKS", "Starbucks", "STARBUCKS"))
        rules = [
            {"id": 1, "pattern": "GEICO EUGENE BRAVERMAN", "category_id": 8},
            {"id": 2, "pattern": "CHEWY", "category_id": 9},
            {"id": 3, "pattern": "CHEWY EUGENE BRAVERMAN", "category_id": 9},
            {"id": 4, "pattern": "STARBUCKS", "category_id": 5},
        ]
        memory = [
            {"id": 11, "merchant_key": "CHEWY", "category_id": 9, "times_used": 5},
            {"id": 12, "merchant_key": "GEICO EUGENE BRAVERMAN", "category_id": 8, "times_used": 3},
            {"id": 13, "merchant_key": "CHEWY EUGENE BRAVERMAN", "category_id": 9, "times_used": 2},
        ]
        self.queries = _stubs.Router([
            ("FROM transactions WHERE user_id = %s ORDER BY account_id", rows),
            ("SELECT fingerprint, occurrence FROM transactions", []),
            ("FROM rules WHERE user_id", rules),
            ("FROM merchant_memory WHERE user_id", memory),
        ])
        self.execs = _stubs.Router(default=1)

    def _run(self):
        with mock.patch.object(maintenance, "db", FAKE), mock.patch.object(FAKE, "query", side_effect=self.queries), \
                mock.patch.object(FAKE, "execute", side_effect=self.execs):
            return maintenance.renormalize_user(4)

    def test_transactions_are_renamed_with_the_cardholder_stripped(self):
        result = self._run()
        self.assertEqual(result["stripped_phrases"], ["EUGENE BRAVERMAN"])
        self.assertEqual((result["transactions_updated"], result["merchant_keys_changed"]), (20, 2))
        updates = self.execs.sql("UPDATE transactions SET merchant_key")
        self.assertEqual(len(updates), 20)
        keys = {p[0] for _s, p in updates}
        self.assertEqual(keys, {"GEICO AUTO", "CHEWY"})
        names = {p[1] for _s, p in updates}
        self.assertEqual(names, {"Geico Auto", "Chewy"})
        self.assertTrue(all(p[4] not in ("old0", "old10") for _s, p in updates))  # fresh fingerprints
        park = self.execs.sql("fingerprint || ':renorm'")
        self.assertEqual(len(park), 1)
        self.assertEqual(sorted(park[0][1][0]), list(range(20)))

    def test_rules_follow_new_keys_and_duplicates_are_removed(self):
        result = self._run()
        self.assertEqual((result["rules_updated"], result["rules_removed_as_duplicates"]), (1, 1))
        rewritten = self.execs.sql("UPDATE rules SET pattern")
        self.assertEqual([p for _s, p in rewritten], [("GEICO AUTO", "GEICO AUTO", 1)])
        deleted = self.execs.sql("DELETE FROM rules WHERE id")
        self.assertEqual([p for _s, p in deleted], [(3,)])
        unlinked = self.execs.sql("SET category_rule_id = NULL")
        self.assertEqual([p for _s, p in unlinked], [(3,)])

    def test_memory_is_renamed_or_merged(self):
        result = self._run()
        self.assertEqual((result["memory_updated"], result["memory_merged"]), (1, 1))
        renamed = self.execs.sql("UPDATE merchant_memory SET merchant_key")
        self.assertEqual([p for _s, p in renamed], [("GEICO AUTO", "Geico Auto", 12)])
        merged = self.execs.sql("SET times_used = times_used + %s")
        self.assertEqual([p for _s, p in merged], [(2, 11)])
        self.assertEqual([p for _s, p in self.execs.sql("DELETE FROM merchant_memory")], [(13,)])

    def test_nothing_to_do_for_a_user_without_transactions(self):
        self.queries = _stubs.Router()
        result = self._run()
        self.assertEqual(result["transactions_updated"], 0)
        self.assertEqual(self.execs.calls, [])


if __name__ == "__main__":
    unittest.main()
