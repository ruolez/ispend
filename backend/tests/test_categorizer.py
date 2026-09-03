import os
import unittest
from contextlib import contextmanager
from decimal import Decimal
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

import db  # noqa: E402  (real module or another test's stub; attributes are patched below)
import categorizer  # noqa: E402
import rules  # noqa: E402


class FakeDB:
    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or (lambda sql, params: None)

    def query(self, sql, params=None, one=False, commit=True):
        self.calls.append(("query", sql, params))
        return self.handler(sql, params)

    def execute(self, sql, params=None, returning=False, commit=True):
        self.calls.append(("execute", sql, params))
        return self.handler(sql, params) if returning else 1

    def execute_values(self, sql, rows, template=None, commit=True, page_size=500):
        self.calls.append(("execute_values", sql, rows))

    @contextmanager
    def transaction(self):
        yield None

    def get_setting(self, key, default=None):
        return default


def patch_db(fake):
    return mock.patch.multiple(db, query=fake.query, execute=fake.execute, execute_values=fake.execute_values,
                               transaction=fake.transaction, get_setting=fake.get_setting, create=True)


def ctx(**kw):
    base = dict(user_id=1, rules=[], memory={}, slug_to_id={"dining.coffee": 5, "groceries": 6, "transfers": 2,
                                                            "transfers.internal": 3, "shopping.online": 8},
                ai_enabled=False, memory_keys=[])
    base.update(kw)
    c = categorizer.Context(**base)
    c.memory_keys = list(c.memory.keys())
    return c


def txn(**kw):
    base = {"description_clean": "STARBUCKS 1234 CHICAGO", "description_raw": "STARBUCKS #1234 CHICAGO IL",
            "merchant_key": "STARBUCKS", "amount": Decimal("-6.45"), "account_id": 1}
    base.update(kw)
    return base


class CascadeTest(unittest.TestCase):
    def test_rule_beats_memory(self):
        rule = rules.compile_rule({"id": 11, "match_type": "contains", "match_field": "description_clean",
                                   "pattern": "starbucks", "category_id": 9, "is_active": True})
        c = ctx(rules=[rule], memory={"STARBUCKS": {"category_id": 5, "is_transfer": False}})
        d = categorizer.categorize(txn(), c)
        self.assertEqual((d.category_id, d.status, d.source, d.rule_id, d.confidence), (9, "confirmed", "rule", 11, 1.0))

    def test_transfer_rule_without_category_uses_transfer_category(self):
        rule = rules.compile_rule({"id": 12, "match_type": "contains", "match_field": "description_clean",
                                   "pattern": "starbucks", "category_id": None, "set_transfer": True, "is_active": True})
        d = categorizer.categorize(txn(), ctx(rules=[rule]))
        self.assertEqual((d.category_id, d.is_transfer, d.is_excluded, d.status), (3, True, True, "confirmed"))

    def test_exact_memory(self):
        d = categorizer.categorize(txn(), ctx(memory={"STARBUCKS": {"category_id": 5, "is_transfer": False}}))
        self.assertEqual((d.category_id, d.status, d.source, d.confidence), (5, "confirmed", "merchant", 0.95))

    def test_fuzzy_memory_is_only_a_suggestion(self):
        d = categorizer.categorize(txn(merchant_key="STARBUCKS COFFEE"),
                                   ctx(memory={"STARBUCKS COFFEE CO": {"category_id": 5, "is_transfer": False}}))
        self.assertEqual((d.category_id, d.status, d.source, d.confidence), (5, "suggested", "merchant", 0.8))

    def test_fuzzy_does_not_match_unrelated(self):
        d = categorizer.categorize(txn(merchant_key="WALGREENS PHARMACY"),
                                   ctx(memory={"STARBUCKS COFFEE": {"category_id": 5, "is_transfer": False}}))
        self.assertNotEqual(d.source, "merchant")

    def test_builtin_hint(self):
        d = categorizer.categorize(txn(), ctx())
        self.assertEqual((d.category_id, d.status, d.source, d.confidence), (5, "suggested", "builtin", 0.6))

    def test_builtin_falls_back_to_parent_slug(self):
        d = categorizer.categorize(txn(merchant_key="AMAZON", description_clean="AMAZON MKTPLACE"),
                                   ctx(slug_to_id={"shopping": 20}))
        self.assertEqual((d.category_id, d.source), (20, "builtin"))

    def test_nothing_matches(self):
        d = categorizer.categorize(txn(merchant_key="ZQX VENDOR", description_clean="ZQX VENDOR 1",
                                       description_raw="ZQX VENDOR 1"), ctx())
        self.assertEqual(d, categorizer.Decision())


class ApplyManualTest(unittest.TestCase):
    def test_updates_records_events_and_learns(self):
        fake = FakeDB(lambda sql, params: [
            {"id": 1, "merchant_key": "STARBUCKS", "merchant_name": "Starbucks"},
            {"id": 2, "merchant_key": "STARBUCKS", "merchant_name": "Starbucks"},
            {"id": 3, "merchant_key": "TIMS", "merchant_name": "Tim Hortons"},
        ] if sql.strip().startswith("SELECT") else None)
        with patch_db(fake):
            n = categorizer.apply_manual(1, [1, 2, 3], 5)
        self.assertEqual(n, 3)
        updates = [c for c in fake.calls if c[0] == "execute" and "UPDATE transactions" in c[1]]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][2], (5, "manual", [1, 2, 3]))
        events = [c for c in fake.calls if c[0] == "execute_values"]
        self.assertEqual(len(events[0][2]), 3)
        learns = [c for c in fake.calls if c[0] == "execute" and "merchant_memory" in c[1]]
        self.assertEqual([c[2][1] for c in learns], ["STARBUCKS", "TIMS"])

    def test_clearing_category_does_not_learn(self):
        fake = FakeDB(lambda sql, params: [{"id": 1, "merchant_key": "X", "merchant_name": "X"}]
                      if sql.strip().startswith("SELECT") else None)
        with patch_db(fake):
            self.assertEqual(categorizer.apply_manual(1, [1], None), 1)
        self.assertEqual([c for c in fake.calls if "merchant_memory" in str(c[1])], [])
        self.assertIn("category_id = NULL", [c for c in fake.calls if c[0] == "execute"][0][1])

    def test_empty_ids(self):
        with patch_db(FakeDB()):
            self.assertEqual(categorizer.apply_manual(1, [], 5), 0)


if __name__ == "__main__":
    unittest.main()
