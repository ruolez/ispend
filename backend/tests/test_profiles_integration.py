"""Profiles never leak into each other, against a real database and the whole app.

One person keeps two profiles with the same default categories; everything in profile B carries
the marker ZZB. Signed in to profile A, every GET endpoint the app has (found by walking the URL
map, so endpoints added later are covered too) is called, with B's ids wherever a route takes an
id, and no response may mention ZZB. Writes aimed at B's rows are refused, and the database never
holds a reference from one profile into another.

    ISPEND_TEST_DSN=postgresql://... python -m unittest tests.test_profiles_integration
"""
import json
import os
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _pg  # noqa: E402

MARK = "ZZB"

# Rows that point into another table must stay inside one profile. Accounts, categories and
# statements are guarded by foreign keys; these references are guarded by the code.
INTEGRITY_SQL = """
SELECT 'split category' AS ref, s.id FROM transaction_splits s
  JOIN transactions t ON t.id = s.transaction_id JOIN categories c ON c.id = s.category_id
 WHERE c.profile_id <> t.profile_id
UNION ALL
SELECT 'transaction tag', tt.transaction_id FROM transaction_tags tt
  JOIN transactions t ON t.id = tt.transaction_id JOIN tags g ON g.id = tt.tag_id
 WHERE g.profile_id <> t.profile_id
UNION ALL
SELECT 'preview row category', r.id FROM import_rows r
  JOIN statements st ON st.id = r.statement_id JOIN categories c ON c.id = r.category_id
 WHERE c.profile_id <> st.profile_id
UNION ALL
SELECT 'preview row duplicate', r.id FROM import_rows r
  JOIN statements st ON st.id = r.statement_id JOIN transactions t ON t.id = r.duplicate_of
 WHERE t.profile_id <> st.profile_id
UNION ALL
SELECT 'transfer pair', a.id FROM transactions a JOIN transactions b ON b.id = a.transfer_pair_id
 WHERE b.profile_id <> a.profile_id
UNION ALL
SELECT 'category rule', t.id FROM transactions t JOIN rules r ON r.id = t.category_rule_id
 WHERE r.profile_id <> t.profile_id
"""


def integrity_problems(db):
    return [(r["ref"], r["id"]) for r in db.query(INTEGRITY_SQL) or []]


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class ProfileIsolationTest(_pg.PgTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import app as app_module
        import profiles

        with mock.patch("billing_tick.start"):
            cls.flask_app = app_module.create_app()
        cls._billing_patch = mock.patch("billing_tick.start")
        cls._billing_patch.start()
        db = cls.db
        cls.uid = db.execute("INSERT INTO users (username, password_hash) VALUES ('pia', 'x') RETURNING id",
                             returning=True)["id"]
        conn = db.get_db()
        cls.a = profiles.create(conn, cls.uid, "Personal")
        cls.b = profiles.create(conn, cls.uid, "Studio", kind="business")
        conn.commit()
        cls.ids_a = cls._books(cls.a, "Everyday")
        cls.ids_b = cls._books(cls.b, MARK)

    @classmethod
    def tearDownClass(cls):
        cls._billing_patch.stop()
        super().tearDownClass()

    @classmethod
    def _books(cls, pid, mark):
        """A little of everything in one profile, every name carrying `mark`."""
        db, uid = cls.db, cls.uid

        def ins(sql, params):
            return db.execute(sql, params, returning=True)["id"]

        cat = db.query("SELECT id FROM categories WHERE profile_id = %s AND slug = 'dining'", (pid,), one=True)["id"]
        own_cat = ins("""INSERT INTO categories (user_id, profile_id, name, slug, kind, color)
                         VALUES (%s, %s, %s, %s, 'expense', 'c3') RETURNING id""", (uid, pid, f"{mark} Supplies", f"{mark.lower()}_supplies"))
        acct = ins("INSERT INTO accounts (user_id, profile_id, name) VALUES (%s, %s, %s) RETURNING id",
                   (uid, pid, f"{mark} Checking"))
        acct2 = ins("INSERT INTO accounts (user_id, profile_id, name, account_type) VALUES (%s, %s, %s, 'credit_card') RETURNING id",
                    (uid, pid, f"{mark} Card"))
        st = ins("""INSERT INTO statements (user_id, profile_id, account_id, original_filename, stored_path, file_sha256,
                                           file_size, file_kind, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 10, 'csv', 'committed') RETURNING id""",
                 (uid, pid, acct, f"{mark}-statement.csv", f"{uid}/{mark}.csv", f"sha-{mark}"))
        rule = ins("""INSERT INTO rules (user_id, profile_id, name, match_type, pattern, category_id)
                      VALUES (%s, %s, %s, 'contains', %s, %s) RETURNING id""", (uid, pid, f"{mark} rule", f"{mark} SHOP", own_cat))
        tag = ins("INSERT INTO tags (user_id, profile_id, name) VALUES (%s, %s, %s) RETURNING id", (uid, pid, f"{mark} trip"))
        txns = []
        for n, (amount, account, desc) in enumerate([("-42.00", acct, f"{mark} SHOP"), ("-15.50", acct, f"{mark} GROCER"),
                                                     ("100.00", acct2, f"{mark} PAYMENT"), ("-100.00", acct, f"{mark} CARD PMT")]):
            txns.append(ins(
                """INSERT INTO transactions (user_id, profile_id, account_id, statement_id, txn_date, amount, currency,
                                            description_raw, description_clean, merchant_key, merchant_name, category_id,
                                            category_status, category_rule_id, fingerprint, notes)
                   VALUES (%s, %s, %s, %s, current_date - %s, %s, 'USD', %s, %s, %s, %s, %s, 'confirmed', %s, %s, %s)
                   RETURNING id""",
                (uid, pid, account, st, n, amount, desc, desc, desc, desc.title(), cat if n else own_cat,
                 rule if n == 0 else None, f"fp-{mark}-{n}", f"{mark} note")))
        db.execute("UPDATE transactions SET transfer_pair_id = %s, is_transfer = TRUE WHERE id = %s", (txns[3], txns[2]))
        db.execute("UPDATE transactions SET transfer_pair_id = %s, is_transfer = TRUE WHERE id = %s", (txns[2], txns[3]))
        db.execute("INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (%s, %s)", (txns[0], tag))
        with db.transaction():
            db.execute("INSERT INTO transaction_splits (transaction_id, category_id, amount) VALUES (%s, %s, -40), (%s, %s, -2)",
                       (txns[0], own_cat, txns[0], cat), commit=False)
        db.execute("""INSERT INTO merchant_memory (user_id, profile_id, merchant_key, display_name, category_id)
                      VALUES (%s, %s, %s, %s, %s)""", (uid, pid, f"{mark} SHOP", f"{mark} Shop", own_cat))
        db.execute("""INSERT INTO budgets (user_id, profile_id, category_id, month, amount, note)
                      VALUES (%s, %s, %s, date_trunc('month', current_date), 300, %s)""", (uid, pid, own_cat, f"{mark} budget"))
        db.execute("""INSERT INTO import_layouts (user_id, profile_id, layout_key, mapping, account_id, sample_filename)
                      VALUES (%s, %s, %s, '{"date": 0}', %s, %s)""", (uid, pid, f"layout-{mark}", acct, f"{mark}.csv"))
        db.execute("INSERT INTO recurring_dismissals (user_id, profile_id, merchant_key) VALUES (%s, %s, %s)",
                   (uid, pid, f"{mark} GYM"))
        return {"account": acct, "category": own_cat, "statement": st, "rule": rule, "tag": tag,
                "transaction": txns[0], "merchant": f"{mark} SHOP"}

    def _client(self, pid):
        c = self.flask_app.test_client()
        with c.session_transaction() as s:
            s.update({"user_id": self.uid, "role": "user", "uepoch": 0, "profile_id": pid})
        return c

    # Route argument -> which of the other profile's ids goes in it; anything else gets a transaction id.
    ARG_KIND = {"account_id": "account", "category_id": "category", "cat_id": "category", "statement_id": "statement",
                "rule_id": "rule", "tag_id": "tag", "txn_id": "transaction", "merchant_key": "merchant"}

    def _fill(self, rule, ids):
        """A concrete path for a URL rule, using the other profile's ids for every argument."""
        path = rule.rule
        for arg in rule.arguments:
            value = ids[self.ARG_KIND.get(arg, "transaction")]
            path = re.sub(rf"<(?:[a-z]+:)?{arg}>", str(value), path)
        return path

    def test_no_get_endpoint_shows_another_profiles_rows(self):
        client = self._client(self.a)
        leaks, checked = [], 0
        for rule in self.flask_app.url_map.iter_rules():
            if "GET" not in rule.methods or not rule.rule.startswith("/api/") or rule.rule.startswith("/api/admin"):
                continue
            path = self._fill(rule, self.ids_b)
            res = client.get(path)
            checked += 1
            if MARK.encode() in res.data or MARK.lower().encode() in res.data:
                leaks.append((path, res.status_code))
            res.close()
        self.assertGreater(checked, 40)
        self.assertEqual(leaks, [])

    def test_the_other_profile_is_there_when_switched_to(self):
        """Proves the canary would see the rows: profile B shows them."""
        client = self._client(self.b)
        self.assertIn(MARK.encode(), client.get("/api/transactions").data)
        self.assertIn(MARK.encode(), client.get("/api/accounts?all=1").data)

    def test_writes_aimed_at_the_other_profile_are_refused(self):
        client = self._client(self.a)
        b = self.ids_b
        cases = [
            ("put", f"/api/transactions/{b['transaction']}", {"notes": "x"}),
            ("post", "/api/transactions/bulk", {"action": "categorize", "ids": [b["transaction"]], "category_id": self.ids_a["category"]}),
            ("put", f"/api/accounts/{b['account']}", {"name": "x"}),
            ("delete", f"/api/categories/{b['category']}", None),
            ("put", f"/api/rules/{b['rule']}", {"pattern": "x"}),
            ("put", f"/api/tags/{b['tag']}", {"name": "x"}),
            ("post", "/api/rules", {"match_type": "contains", "pattern": "x", "category_id": b["category"]}),
            ("post", "/api/budgets", {"category_id": b["category"], "amount": 10}),
            ("post", "/api/transactions", {"account_id": b["account"], "txn_date": "2026-01-02", "amount": "-1",
                                           "description": "x"}),
            ("post", "/api/review/resolve", {"ids": [b["transaction"]], "category_id": self.ids_a["category"]}),
            ("delete", f"/api/statements/{b['statement']}", None),
        ]
        before = self.db.query("SELECT COUNT(*) AS n, MAX(updated_at) AS t FROM transactions WHERE profile_id = %s",
                               (self.b,), one=True)
        statuses = {}
        for method, path, body in cases:
            res = getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)
            statuses[f"{method} {path}"] = res.status_code
        self.assertEqual({k: v for k, v in statuses.items() if v not in (400, 404)}, {})
        self.assertEqual(self.db.query("SELECT COUNT(*) AS n, MAX(updated_at) AS t FROM transactions WHERE profile_id = %s",
                                       (self.b,), one=True), before)

    def test_a_stale_tab_is_told_the_profile_changed(self):
        client = self._client(self.a)
        res = client.get("/api/transactions", headers={"X-Profile": str(self.b)})
        self.assertEqual((res.status_code, res.get_json()["code"], res.get_json()["profile_id"]),
                         (409, "profile_changed", self.a))

    def test_no_reference_crosses_profiles(self):
        self.assertEqual(integrity_problems(self.db), [])

    def test_the_database_refuses_a_cross_profile_reference(self):
        import psycopg2
        with self.assertRaises(psycopg2.errors.ForeignKeyViolation):
            self.db.execute("UPDATE transactions SET category_id = %s WHERE id = %s",
                            (self.ids_b["category"], self.ids_a["transaction"]))
        self.db.get_db().rollback()



@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class ProfileManagementTest(_pg.PgTestCase):
    """Creating, copying, renaming and deleting profiles through the API."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import app as app_module
        import profiles

        cls._billing_patch = mock.patch("billing_tick.start")
        cls._billing_patch.start()
        cls.flask_app = app_module.create_app()
        cls.uid = cls.db.execute("INSERT INTO users (username, password_hash) VALUES ('max', 'x') RETURNING id",
                                 returning=True)["id"]
        conn = cls.db.get_db()
        cls.home = profiles.create(conn, cls.uid)
        conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls._billing_patch.stop()
        super().tearDownClass()

    def _client(self, pid=None):
        c = self.flask_app.test_client()
        with c.session_transaction() as s:
            s.update({"user_id": self.uid, "role": "user", "uepoch": 0, "profile_id": pid or self.home})
        return c

    def _slugs(self, pid):
        return {r["slug"] for r in self.db.query("SELECT slug FROM categories WHERE profile_id = %s", (pid,))}

    def test_a_business_profile_starts_with_business_categories(self):
        res = self._client().post("/api/profiles", json={"name": "Studio", "kind": "business", "color": "c4"})
        self.assertEqual(res.status_code, 201, res.get_json())
        slugs = self._slugs(res.get_json()["id"])
        # what transfer pairing and the built-in hints look for, plus the business's own
        self.assertTrue({"transfers", "transfers.card_payment", "transfers.internal", "transfers.owner", "income.sales",
                         "fees.bank", "subscriptions.software", "office.supplies"} <= slugs, slugs)
        self.assertNotIn("groceries", slugs)

    def test_a_copy_brings_categories_rules_tags_and_memory_but_no_money(self):
        db = self.db
        dining = db.query("SELECT id FROM categories WHERE profile_id = %s AND slug = 'dining'", (self.home,), one=True)["id"]
        acct = db.execute("INSERT INTO accounts (user_id, profile_id, name) VALUES (%s, %s, 'Joint') RETURNING id",
                          (self.uid, self.home), returning=True)["id"]
        db.execute("""INSERT INTO rules (user_id, profile_id, name, match_type, pattern, category_id)
                      VALUES (%s, %s, 'cafes', 'contains', 'CAFE', %s)""", (self.uid, self.home, dining))
        db.execute("""INSERT INTO rules (user_id, profile_id, name, match_type, pattern, category_id, account_id)
                      VALUES (%s, %s, 'joint only', 'contains', 'X', %s, %s)""", (self.uid, self.home, dining, acct))
        db.execute("INSERT INTO tags (user_id, profile_id, name) VALUES (%s, %s, 'Trip')", (self.uid, self.home))
        db.execute("""INSERT INTO merchant_memory (user_id, profile_id, merchant_key, category_id)
                      VALUES (%s, %s, 'BLUE BOTTLE', %s)""", (self.uid, self.home, dining))
        res = self._client().post("/api/profiles", json={"name": "Copy", "start": "copy", "copy_from": self.home})
        self.assertEqual(res.status_code, 201, res.get_json())
        pid = res.get_json()["id"]
        got = db.query("""SELECT (SELECT COUNT(*) FROM categories WHERE profile_id = %(p)s) AS categories,
                                 (SELECT COUNT(*) FROM categories WHERE profile_id = %(h)s) AS source_categories,
                                 (SELECT array_agg(pattern) FROM rules WHERE profile_id = %(p)s) AS rules,
                                 (SELECT array_agg(name) FROM tags WHERE profile_id = %(p)s) AS tags,
                                 (SELECT array_agg(c.slug) FROM merchant_memory m JOIN categories c ON c.id = m.category_id
                                   WHERE m.profile_id = %(p)s) AS memory,
                                 (SELECT COUNT(*) FROM accounts WHERE profile_id = %(p)s) AS accounts""",
                       {"p": pid, "h": self.home}, one=True)
        self.assertEqual((got["categories"], got["rules"], got["tags"], got["memory"], got["accounts"]),
                         (got["source_categories"], ["CAFE"], ["Trip"], ["dining"], 0))
        self.assertEqual(integrity_problems(db), [])

    def test_names_are_unique_per_person_and_validated(self):
        c = self._client()
        self.assertEqual(c.post("/api/profiles", json={"name": "personal"}).get_json()["error"],
                         "You already have a profile with that name")
        self.assertEqual(c.post("/api/profiles", json={"name": "  "}).status_code, 400)
        self.assertEqual(c.post("/api/profiles", json={"name": "X", "kind": "charity"}).status_code, 400)

    def test_rename_and_recolor(self):
        pid = self._client().post("/api/profiles", json={"name": "Side gig"}).get_json()["id"]
        res = self._client().put(f"/api/profiles/{pid}", json={"name": "Side Hustle", "color": "c9"})
        self.assertEqual((res.status_code, res.get_json()["name"], res.get_json()["color"]), (200, "Side Hustle", "c9"))

    def test_delete_needs_the_name_and_takes_everything_with_it(self):
        import config
        db = self.db
        pid = self._client().post("/api/profiles", json={"name": "Old shop", "kind": "business"}).get_json()["id"]
        acct = db.execute("INSERT INTO accounts (user_id, profile_id, name) VALUES (%s, %s, 'Shop') RETURNING id",
                          (self.uid, pid), returning=True)["id"]
        folder = os.path.join(config.STATEMENTS_DIR, str(self.uid))
        os.makedirs(folder, exist_ok=True)
        for name in ("only-here.csv", "shared.csv"):
            with open(os.path.join(folder, name), "w") as f:
                f.write("date,amount\n")
        for st_pid, name in ((pid, "only-here.csv"), (pid, "shared.csv"), (self.home, "shared.csv")):
            db.execute("""INSERT INTO statements (user_id, profile_id, account_id, original_filename, stored_path, file_sha256,
                                                 file_size, file_kind, status)
                          VALUES (%s, %s, %s, %s, %s, %s, 10, 'csv', 'committed')""",
                       (self.uid, st_pid, acct if st_pid == pid else None, name, f"{self.uid}/{name}", name))
        client = self._client(pid)
        self.assertEqual(client.delete(f"/api/profiles/{pid}", json={"confirm": "old"}).status_code, 400)
        res = client.delete(f"/api/profiles/{pid}", json={"confirm": "old shop"})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual((res.get_json()["accounts"], res.get_json()["statements"]), (1, 2))
        self.assertEqual(res.get_json()["active_profile_id"], self.home, "the session moves to another profile")
        self.assertEqual([os.path.exists(os.path.join(folder, n)) for n in ("only-here.csv", "shared.csv")], [False, True])
        self.assertIsNone(db.query("SELECT 1 FROM accounts WHERE id = %s", (acct,), one=True))

    def test_the_last_profile_cannot_be_deleted(self):
        other = self.db.execute("INSERT INTO users (username, password_hash) VALUES ('solo', 'x') RETURNING id",
                                returning=True)["id"]
        import profiles
        conn = self.db.get_db()
        pid = profiles.create(conn, other)
        conn.commit()
        c = self.flask_app.test_client()
        with c.session_transaction() as s:
            s.update({"user_id": other, "role": "user", "uepoch": 0, "profile_id": pid})
        res = c.delete(f"/api/profiles/{pid}", json={"confirm": "Personal"})
        self.assertEqual(res.status_code, 409)

    def test_someone_elses_profile_is_not_found(self):
        stranger = self.db.execute("INSERT INTO users (username, password_hash) VALUES ('eve', 'x') RETURNING id",
                                   returning=True)["id"]
        c = self.flask_app.test_client()
        with c.session_transaction() as s:
            s.update({"user_id": stranger, "role": "user", "uepoch": 0})
        self.assertEqual(c.put(f"/api/profiles/{self.home}", json={"name": "mine"}).status_code, 404)
        self.assertEqual(c.post("/api/auth/me/profile", json={"id": self.home}).status_code, 404)
        self.assertEqual(c.post("/api/profiles", json={"name": "x", "start": "copy", "copy_from": self.home}).status_code, 404)


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class MoveAccountsTest(_pg.PgTestCase):
    """Moving accounts from Personal into a Business profile. Each test builds its own person."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import app as app_module
        cls._billing_patch = mock.patch("billing_tick.start")
        cls._billing_patch.start()
        cls.flask_app = app_module.create_app()
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls._billing_patch.stop()
        super().tearDownClass()

    def setUp(self):
        import profiles
        db = self.db
        type(self).n += 1
        self.uid = uid = db.execute("INSERT INTO users (username, password_hash) VALUES (%s, 'x') RETURNING id",
                                    (f"mover{self.n}",), returning=True)["id"]
        conn = db.get_db()
        self.a = a = profiles.create(conn, uid, "Personal")
        self.b = profiles.create(conn, uid, "Studio", kind="business")
        conn.commit()

        def ins(sql, params):
            return db.execute(sql, params, returning=True)["id"]

        def cat(slug, pid=a):
            return db.query("SELECT id FROM categories WHERE profile_id = %s AND slug = %s", (pid, slug), one=True)["id"]

        self.cat = cat
        acct = lambda name, kind="checking": ins(  # noqa: E731
            "INSERT INTO accounts (user_id, profile_id, name, account_type) VALUES (%s, %s, %s, %s) RETURNING id",
            (uid, a, name, kind))
        self.x, self.y, self.z = acct("Everyday"), acct("Visa", "credit_card"), acct("Savings", "savings")
        self.side = ins("""INSERT INTO categories (user_id, profile_id, name, slug, kind) VALUES (%s, %s, 'Side gigs', 'side_gigs', 'expense')
                           RETURNING id""", (uid, a))
        self.office_income = ins("""INSERT INTO categories (user_id, profile_id, name, slug, kind)
                                    VALUES (%s, %s, 'Office', 'office', 'income') RETURNING id""", (uid, a))
        self.s1 = ins("""INSERT INTO statements (user_id, profile_id, account_id, original_filename, stored_path, file_sha256,
                                                file_size, file_kind, status) VALUES (%s, %s, %s, 'oct.csv', 'x/oct.csv', 'sha1', 1, 'csv', 'committed')
                         RETURNING id""", (uid, a, self.x))
        self.s_visa = ins("""INSERT INTO statements (user_id, profile_id, account_id, original_filename, stored_path, file_sha256,
                                                    file_size, file_kind, status) VALUES (%s, %s, %s, 'visa.csv', 'x/visa.csv', 'sha2', 1, 'csv', 'committed')
                             RETURNING id""", (uid, a, self.y))
        self.k = 0

        def txn(account, amount, key, category=None, statement=None):
            self.k += 1
            return ins("""INSERT INTO transactions (user_id, profile_id, account_id, statement_id, txn_date, amount, currency,
                                                    description_raw, description_clean, merchant_key, merchant_name, category_id,
                                                    category_status, fingerprint)
                          VALUES (%s, %s, %s, %s, '2026-10-01', %s, 'USD', %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                       (uid, a, account, statement, amount, key, key, key, key, category,
                        "confirmed" if category else "none", f"fp{self.k}"))

        self.coffee = txn(self.x, "-4.50", "BLUE BOTTLE", cat("dining.coffee"), self.s1)
        self.gig = txn(self.x, "-40.00", "UPWORK FEE", self.side, self.s1)
        self.split = txn(self.x, "-100.00", "COSTCO", cat("groceries"), self.s1)
        self.odd = txn(self.x, "75.00", "REIMBURSED", self.office_income)
        self.stray = txn(self.x, "-9.00", "LATE FEE", cat("fees.bank"), self.s_visa)   # statement of an account that stays
        self.pay_out = txn(self.x, "-200.00", "PAYMENT TO VISA", cat("transfers.card_payment"))
        self.pay_in = txn(self.y, "200.00", "PAYMENT THANK YOU", cat("transfers.card_payment"), self.s_visa)
        self.save_out = txn(self.x, "-50.00", "TRANSFER TO SAVINGS", cat("transfers.internal"))
        self.save_in = txn(self.z, "50.00", "TRANSFER FROM CHECKING", cat("transfers.internal"))
        for one, two in ((self.pay_out, self.pay_in), (self.save_out, self.save_in)):
            db.execute("UPDATE transactions SET transfer_pair_id = %s WHERE id = %s", (two, one))
            db.execute("UPDATE transactions SET transfer_pair_id = %s WHERE id = %s", (one, two))
        with db.transaction():
            db.execute("""INSERT INTO transaction_splits (transaction_id, category_id, amount) VALUES (%s, %s, -60), (%s, %s, -40)""",
                       (self.split, cat("groceries"), self.split, self.side), commit=False)
        self.trip = ins("INSERT INTO tags (user_id, profile_id, name) VALUES (%s, %s, 'Trip') RETURNING id", (uid, a))
        db.execute("INSERT INTO transaction_tags (transaction_id, tag_id) VALUES (%s, %s)", (self.coffee, self.trip))
        self.rule = ins("""INSERT INTO rules (user_id, profile_id, name, match_type, pattern, category_id, account_id)
                           VALUES (%s, %s, 'gigs', 'contains', 'UPWORK', %s, %s) RETURNING id""", (uid, a, self.side, self.x))
        db.execute("UPDATE transactions SET category_rule_id = %s WHERE id = %s", (self.rule, self.gig))
        db.execute("""INSERT INTO merchant_memory (user_id, profile_id, merchant_key, direction, category_id)
                      VALUES (%s, %s, 'BLUE BOTTLE', 'out', %s)""", (uid, a, cat("dining.coffee")))
        db.execute("""INSERT INTO import_layouts (user_id, profile_id, layout_key, mapping, account_id)
                      VALUES (%s, %s, 'k', '{"date": 0}', %s)""", (uid, a, self.x))
        self.preview_st = ins("""INSERT INTO statements (user_id, profile_id, account_id, original_filename, stored_path, file_sha256,
                                                         file_size, file_kind, status)
                                 VALUES (%s, %s, %s, 'nov.csv', 'x/nov.csv', 'sha3', 1, 'csv', 'previewed') RETURNING id""",
                              (uid, a, self.x))
        db.execute("""INSERT INTO import_rows (statement_id, row_index, raw, category_id, duplicate_of)
                      VALUES (%s, 0, '{}', %s, %s)""", (self.preview_st, self.side, self.coffee))
        db.execute("""UPDATE profiles SET preferences = %s WHERE id = %s""",
                   (json.dumps({"default_account_id": self.x,
                                "saved_views": [{"id": "v1", "name": "Both", "query": f"?acct={self.x},{self.y}&range=all",
                                                 "pinned": True, "page": "transactions"}]}), a))

    def _client(self):
        c = self.flask_app.test_client()
        with c.session_transaction() as s:
            s.update({"user_id": self.uid, "role": "user", "uepoch": 0, "profile_id": self.a})
        return c

    def _row(self, sql, *params):
        return self.db.query(sql, params, one=True)

    def test_the_preview_says_what_will_happen_and_writes_nothing(self):
        res = self._client().post(f"/api/profiles/{self.b}/move-preview", json={"account_ids": [self.x]})
        self.assertEqual(res.status_code, 200, res.get_json())
        p = res.get_json()
        self.assertEqual((p["counts"]["transactions"], p["counts"]["statements"], p["counts"]["rules"],
                          p["counts"]["transfers_unlinked"], p["collisions"], p["busy"]),
                         (7, 2, 1, 2, [], []))
        self.assertEqual(sorted(p["categories_created"]), ["Groceries", "Office", "Side gigs"])
        self.assertIn("Coffee", p["categories_matched"])
        self.assertEqual(p["tags_created"], ["Trip"])
        self.assertEqual(self._row("SELECT profile_id FROM accounts WHERE id = %s", self.x)["profile_id"], self.a)

    def test_moving_one_account_brings_its_rows_and_unlinks_transfers_that_stay_behind(self):
        res = self._client().post(f"/api/profiles/{self.b}/move", json={"account_ids": [self.x]})
        self.assertEqual(res.status_code, 200, res.get_json())
        db, b = self.db, self.b
        self.assertEqual(db.query("SELECT DISTINCT profile_id FROM transactions WHERE account_id = %s", (self.x,)), [{"profile_id": b}])
        self.assertEqual([r["profile_id"] for r in db.query("SELECT profile_id FROM statements WHERE account_id = %s", (self.x,))], [b, b])
        moved = {r["id"]: r for r in db.query(
            """SELECT t.id, t.statement_id, t.transfer_pair_id, t.is_transfer, t.category_rule_id, c.slug, c.kind, c.profile_id AS cat_profile
                 FROM transactions t LEFT JOIN categories c ON c.id = t.category_id WHERE t.account_id = %s""", (self.x,))}
        self.assertEqual({k: moved[k]["slug"] for k in (self.coffee, self.gig, self.split, self.odd)},
                         {self.coffee: "dining.coffee", self.gig: "side_gigs", self.split: "groceries", self.odd: "office_2"})
        self.assertEqual(moved[self.odd]["kind"], "income", "an income category never becomes the business's expense one")
        self.assertEqual({r["cat_profile"] for r in moved.values()}, {b})
        self.assertEqual(moved[self.gig]["category_rule_id"], self.rule, "the account's own rule came along")
        self.assertIsNone(moved[self.stray]["statement_id"], "its statement belongs to an account that stayed")
        for out, other in ((self.pay_out, self.pay_in), (self.save_out, self.save_in)):
            self.assertEqual((moved[out]["transfer_pair_id"], moved[out]["is_transfer"]), (None, True))
            self.assertEqual(self._row("SELECT transfer_pair_id FROM transactions WHERE id = %s", other)["transfer_pair_id"], None)
        self.assertEqual(self._row("SELECT profile_id FROM rules WHERE id = %s", self.rule)["profile_id"], b)
        self.assertEqual(self._row("SELECT profile_id FROM import_layouts WHERE account_id = %s", self.x)["profile_id"], b)
        self.assertEqual(self._row("""SELECT c.slug FROM merchant_memory m JOIN categories c ON c.id = m.category_id
                                       WHERE m.profile_id = %s AND m.merchant_key = 'BLUE BOTTLE'""", b)["slug"], "dining.coffee")
        self.assertEqual(self._row("""SELECT g.name, g.profile_id FROM transaction_tags tt JOIN tags g ON g.id = tt.tag_id
                                       WHERE tt.transaction_id = %s""", self.coffee), {"name": "Trip", "profile_id": b})
        self.assertEqual(self._row("SELECT duplicate_of FROM import_rows WHERE statement_id = %s", self.preview_st)["duplicate_of"],
                         self.coffee, "the preview's duplicate moved with it")
        prefs = self._row("SELECT preferences FROM profiles WHERE id = %s", self.a)["preferences"]
        self.assertEqual((prefs.get("default_account_id"), prefs["saved_views"][0]["query"]),
                         (None, f"?acct={self.y}&range=all"))
        self.assertEqual(integrity_problems(db), [])

    def test_moving_both_ends_of_a_transfer_keeps_the_pair(self):
        res = self._client().post(f"/api/profiles/{self.b}/move", json={"account_ids": [self.x, self.z]})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(res.get_json()["transfers_unlinked"], 1, "only the card payment's other leg stayed behind")
        self.assertEqual(self._row("SELECT transfer_pair_id FROM transactions WHERE id = %s", self.save_out)["transfer_pair_id"],
                         self.save_in)
        self.assertEqual(integrity_problems(self.db), [])

    def test_a_name_the_target_already_uses_needs_a_new_one(self):
        self.db.execute("INSERT INTO accounts (user_id, profile_id, name) VALUES (%s, %s, 'Everyday')", (self.uid, self.b))
        c = self._client()
        res = c.post(f"/api/profiles/{self.b}/move", json={"account_ids": [self.x]})
        self.assertEqual(res.status_code, 409)
        res = c.post(f"/api/profiles/{self.b}/move", json={"account_ids": [self.x], "rename": {str(self.x): "Everyday (personal)"}})
        self.assertEqual(res.status_code, 200, res.get_json())
        self.assertEqual(self._row("SELECT name FROM accounts WHERE id = %s", self.x)["name"], "Everyday (personal)")

    def test_an_import_still_running_blocks_the_move(self):
        self.db.execute("UPDATE statements SET status = 'parsing' WHERE id = %s", (self.preview_st,))
        res = self._client().post(f"/api/profiles/{self.b}/move", json={"account_ids": [self.x]})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self._row("SELECT profile_id FROM accounts WHERE id = %s", self.x)["profile_id"], self.a)

    def test_a_failure_halfway_leaves_everything_where_it_was(self):
        import profile_move
        before = self.db.query("SELECT COUNT(*) AS n FROM categories WHERE profile_id = %s", (self.b,), one=True)
        with mock.patch.object(profile_move, "_forget_accounts", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                with self.flask_app.test_request_context():
                    profile_move.move(self.uid, self.a, self.b, [self.x])
        self.db.get_db().rollback()
        self.assertEqual(self._row("SELECT profile_id FROM accounts WHERE id = %s", self.x)["profile_id"], self.a)
        self.assertEqual(self.db.query("SELECT COUNT(*) AS n FROM categories WHERE profile_id = %s", (self.b,), one=True), before)
        self.assertEqual(self._row("SELECT transfer_pair_id FROM transactions WHERE id = %s", self.pay_out)["transfer_pair_id"],
                         self.pay_in)

    def test_only_accounts_of_the_active_profile_into_another_of_ones_own(self):
        c = self._client()
        self.assertEqual(c.post(f"/api/profiles/{self.a}/move", json={"account_ids": [self.x]}).status_code, 400)
        other = self.db.execute("INSERT INTO users (username, password_hash) VALUES (%s, 'x') RETURNING id",
                                (f"other{self.n}",), returning=True)["id"]
        import profiles
        conn = self.db.get_db()
        theirs = profiles.create(conn, other)
        conn.commit()
        self.assertEqual(c.post(f"/api/profiles/{theirs}/move", json={"account_ids": [self.x]}).status_code, 404)
        b_acct = self.db.execute("INSERT INTO accounts (user_id, profile_id, name) VALUES (%s, %s, 'Biz') RETURNING id",
                                 (self.uid, self.b), returning=True)["id"]
        self.assertEqual(c.post(f"/api/profiles/{self.b}/move", json={"account_ids": [b_acct]}).status_code, 404)

if __name__ == "__main__":
    unittest.main()
