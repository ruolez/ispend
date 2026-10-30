"""Merchant memory is kept per direction (money in / money out) against a real database: the
029 migration splits what was learned before, and learning one direction leaves the other alone.

    ISPEND_TEST_DSN=postgresql://... python -m unittest tests.test_merchant_direction_integration
"""
import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.dirname(__file__))
import _pg  # noqa: E402

MIGRATION = os.path.join(os.path.dirname(__file__), "..", "migrations", "029_merchant_memory_direction.sql")
UNDO_029 = """
    ALTER TABLE merchant_memory DROP CONSTRAINT merchant_memory_user_key_direction_key;
    ALTER TABLE merchant_memory DROP COLUMN direction;
    ALTER TABLE merchant_memory ADD CONSTRAINT merchant_memory_user_id_merchant_key_key UNIQUE (user_id, merchant_key);
"""


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class MerchantDirectionTest(_pg.PgTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        db = cls.db
        cls.uid = db.execute("INSERT INTO users (username, password_hash) VALUES ('mm', 'x') RETURNING id",
                             returning=True)["id"]
        from scope import Scope
        cls.scope = Scope(cls.uid, db.execute("INSERT INTO profiles (user_id, name) VALUES (%s, 'Personal') RETURNING id",
                                              (cls.uid,), returning=True)["id"])
        cls.acct = db.execute("INSERT INTO accounts (user_id, name) VALUES (%s, 'Checking') RETURNING id",
                              (cls.uid,), returning=True)["id"]

        def cat(name, kind):
            return db.execute("INSERT INTO categories (user_id, name, slug, kind) VALUES (%s, %s, %s, %s) RETURNING id",
                              (cls.uid, name, name.lower(), kind), returning=True)["id"]

        cls.income = cat("Salary", "income")
        cls.fees = cat("Fees", "expense")
        cls.coffee = cat("Coffee", "expense")
        cls.gifts = cat("Gifts", "income")

    def setUp(self):
        self.db.execute("DELETE FROM merchant_memory")
        self.db.execute("DELETE FROM transactions")
        self.n = 0

    def txn(self, key, amount, category_id, source="manual"):
        self.n += 1
        self.db.execute(
            """INSERT INTO transactions (user_id, account_id, txn_date, amount, currency, description_raw,
                   description_clean, merchant_key, merchant_name, category_id, category_status, category_source,
                   fingerprint)
               VALUES (%s, %s, '2026-09-01', %s, 'USD', %s, %s, %s, %s, %s, 'confirmed', %s, %s)""",
            (self.uid, self.acct, Decimal(amount), key, key, key, key, category_id, source, f"fp{self.n}"))

    def memory(self):
        rows = self.db.query("SELECT merchant_key, direction, category_id FROM merchant_memory WHERE user_id = %s",
                             (self.uid,)) or []
        return sorted((r["merchant_key"], r["direction"], r["category_id"]) for r in rows)

    def rerun_migration(self, old_memory):
        """old_memory: (merchant_key, category_id) rows in the pre-029 shape, one per merchant."""
        self.db.execute(UNDO_029)
        for key, cid in old_memory:
            self.db.execute("INSERT INTO merchant_memory (user_id, merchant_key, category_id) VALUES (%s, %s, %s)",
                            (self.uid, key, cid))
        with open(MIGRATION) as f:
            self.db.execute(f.read())

    def test_migration_splits_a_merchant_the_user_sorted_both_ways(self):
        # the reported case: deposits were sorted as Salary by hand, the memory then auto-filed the
        # fees as Salary too, and the user's fix on a fee overwrote the memory with Fees
        self.txn("ACME PAYROLL", "1500.00", self.income)
        self.txn("ACME PAYROLL", "1500.00", self.income)
        self.txn("ACME PAYROLL", "-25.00", self.income, source="merchant")
        self.txn("ACME PAYROLL", "-25.00", self.income, source="merchant")
        self.txn("ACME PAYROLL", "-25.00", self.fees)
        self.txn("ACME PAYROLL", "900.00", self.fees, source="merchant")
        self.rerun_migration([("ACME PAYROLL", self.fees)])
        self.assertEqual(self.memory(), [("ACME PAYROLL", "in", self.income), ("ACME PAYROLL", "out", self.fees)])

    def test_migration_keeps_one_direction_when_history_has_one(self):
        self.txn("BLUE BOTTLE", "-4.50", self.coffee)
        self.txn("BLUE BOTTLE", "-5.00", self.coffee, source="merchant")
        self.rerun_migration([("BLUE BOTTLE", self.coffee)])
        self.assertEqual(self.memory(), [("BLUE BOTTLE", "out", self.coffee)])

    def test_migration_without_history_follows_the_category_kind(self):
        self.rerun_migration([("GRANDMA", self.gifts), ("CORNER CAFE", self.coffee)])
        self.assertEqual(self.memory(), [("CORNER CAFE", "out", self.coffee), ("GRANDMA", "in", self.gifts)])

    def test_migration_moves_memory_to_the_direction_its_history_is_in(self):
        # an expense category the user only ever used for refunds coming in
        self.txn("STORE REFUNDS", "30.00", self.fees)
        self.rerun_migration([("STORE REFUNDS", self.fees)])
        self.assertEqual(self.memory(), [("STORE REFUNDS", "in", self.fees)])

    def test_learning_one_direction_leaves_the_other_alone(self):
        import categorizer
        categorizer.learn(self.scope, "ACME PAYROLL", self.income, "in")
        categorizer.learn(self.scope, "ACME PAYROLL", self.fees, "out")
        categorizer.learn(self.scope, "ACME PAYROLL", self.income, "in")
        self.assertEqual(self.memory(), [("ACME PAYROLL", "in", self.income), ("ACME PAYROLL", "out", self.fees)])
        ctx = categorizer.load_context(self.scope)
        got = [categorizer.categorize({"merchant_key": "ACME PAYROLL", "amount": Decimal(a)}, ctx).category_id
               for a in ("1500.00", "-25.00")]
        self.assertEqual(got, [self.income, self.fees])

    def test_a_merchant_remembered_one_way_is_not_applied_the_other_way(self):
        import categorizer
        categorizer.learn(self.scope, "ACME PAYROLL", self.income, "in")
        d = categorizer.categorize({"merchant_key": "ACME PAYROLL", "amount": Decimal("-25.00"),
                                    "description_clean": "ACME PAYROLL", "description_raw": "ACME PAYROLL"},
                                   categorizer.load_context(self.scope))
        self.assertEqual((d.category_id, d.source), (None, None))


if __name__ == "__main__":
    unittest.main()
