import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import categories_api  # noqa: E402
import seed_categories  # noqa: E402
import util  # noqa: E402


CAT = {"id": 3, "user_id": 1, "parent_id": None, "name": "Dining", "slug": "dining", "kind": "expense",
       "color": "c2", "icon": "utensils", "is_system": True, "sort_order": 3}


class CategoriesApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(categories_api.bp)
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(categories_api, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None, content_type="application/json")

    def test_create_validation(self):
        cases = [({}, 400, "Name is required"), ({"name": "X", "color": "red"}, 400, "Invalid color slot"),
                 ({"name": "X", "kind": "loan"}, 400, "Invalid kind"), ({"name": "X", "parent_id": 99}, 404, "Parent category not found")]
        for body, status, msg in cases:
            res = self._call("post", "/api/categories", body)
            self.assertEqual((res.status_code, res.get_json()["error"]), (status, msg), body)

    def test_create_subcategory_inherits_parent_and_builds_slug(self):
        self.q.routes += [("SELECT * FROM categories WHERE id = %s AND user_id", CAT),
                          ("MAX(sort_order)", {"n": 4}),
                          ("SELECT id, parent_id, name, slug, kind, color, icon, is_system, sort_order FROM categories WHERE id",
                           {**CAT, "id": 10, "parent_id": 3, "name": "Coffee", "slug": "dining.coffee", "is_system": False})]
        self.x.routes.append(("INSERT INTO categories", {"id": 10}))
        res = self._call("post", "/api/categories", {"name": "Coffee", "parent_id": 3})
        self.assertEqual(res.status_code, 201)
        sql, params = self.x.sql("INSERT INTO categories")[0]
        self.assertEqual(params, (1, 3, "Coffee", "dining.coffee", "expense", "c2", "utensils", 4))

    def test_delete_refuses_while_referenced(self):
        self.q.routes += [("SELECT * FROM categories WHERE id", CAT), ("SELECT id FROM categories WHERE parent_id", [{"id": 4}]),
                          ("AS transactions", {"transactions": 7, "rules": 2, "merchants": 0})]
        res = self._call("delete", "/api/categories/3")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.get_json()["references"], {"transactions": 7, "rules": 2, "merchants": 0})
        self.assertIn("7 transactions, 2 rules", res.get_json()["error"])
        self.assertEqual(self.x.sql("DELETE FROM categories"), [])

    def test_delete_counts_split_lines_as_references(self):
        self.q.routes += [("SELECT * FROM categories WHERE id", CAT), ("SELECT id FROM categories WHERE parent_id", []),
                          ("AS transactions", {"transactions": 0, "rules": 0, "merchants": 0, "splits": 3})]
        res = self._call("delete", "/api/categories/3")
        self.assertEqual(res.status_code, 409)
        self.assertIn("3 split lines still use this category", res.get_json()["error"])
        self.assertEqual(self.q.sql("AS splits")[0][1], (1, [3], 1, [3], 1, [3], 1, [3]))

    def test_delete_with_reassignment_moves_everything_then_deletes_children_too(self):
        self.q.routes += [("SELECT * FROM categories WHERE id", CAT), ("SELECT id FROM categories WHERE parent_id", [{"id": 4}]),
                          ("AS transactions", {"transactions": 7, "rules": 2, "merchants": 1}),
                          ("SELECT id FROM categories WHERE id = %s AND user_id", {"id": 8})]
        res = self._call("delete", "/api/categories/3?reassign_to=8")
        self.assertEqual(res.status_code, 200)
        moved = [(s.split(" SET")[0], p) for s, p in self.x.calls if s.startswith("UPDATE")]
        self.assertEqual(moved[:3], [("UPDATE transactions", (8, [3, 4], 1)), ("UPDATE rules", (8, [3, 4], 1)),
                                     ("UPDATE import_rows", (8, [3, 4]))])
        self.assertEqual([p for _s, p in self.x.sql("UPDATE merchant_memory SET category_id")], [(8, [3, 4], 1)])
        self.assertEqual([p for _s, p in self.x.sql("UPDATE transaction_splits s SET category_id")], [(8, [3, 4], 1)])
        self.assertEqual([p for _s, p in self.x.sql("DELETE FROM categories")], [([3, 4],)])

    def test_reassign_target_must_be_owned_and_different(self):
        self.q.routes += [("SELECT * FROM categories WHERE id", CAT), ("SELECT id FROM categories WHERE parent_id", []),
                          ("AS transactions", {"transactions": 1, "rules": 0, "merchants": 0}),
                          ("SELECT id FROM categories WHERE id = %s AND user_id", {"id": 3})]
        res = self._call("delete", "/api/categories/3?reassign_to=3")
        self.assertEqual(res.status_code, 404)

    def test_reorder_writes_positions_scoped_to_user(self):
        res = self._call("put", "/api/categories/reorder", {"ids": [9, "7", "x", 5]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p for _s, p in self.x.sql("UPDATE categories SET sort_order")], [(0, 9, 1), (1, 7, 1), (2, 5, 1)])

    def test_reset_defaults_seeds_and_returns_tree(self):
        with mock.patch.object(seed_categories, "seed_for_user") as seed, \
                mock.patch.object(categories_api, "load_tree", return_value=[{"id": 1, "children": []}]):
            res = self._call("post", "/api/categories/reset-defaults")
        self.assertEqual(res.get_json(), [{"id": 1, "children": []}])
        self.assertEqual(seed.call_args.args, (FAKE, 1))

    def test_unknown_category_is_404_for_update_and_merge(self):
        self.assertEqual(self._call("put", "/api/categories/55", {"name": "x"}).status_code, 404)
        self.assertEqual(self._call("post", "/api/categories/55/merge", {"into": 3}).status_code, 400)


class SeedFindTest(unittest.TestCase):
    """seed_for_user must skip defaults whose name already exists under the same parent."""

    def test_existing_name_counts_as_present(self):
        class Cur:
            def __init__(self):
                self.sql = []
                self._next = None

            def execute(self, sql, params=None):
                self.sql.append(" ".join(sql.split()))
                if "slug = %s" in sql:
                    self._next = None
                elif "lower(name) = lower(%s)" in sql:
                    self._next = (42,) if params[-1] == "Groceries" else None
                elif sql.strip().startswith("INSERT"):
                    self._next = (100,)

            def fetchone(self):
                return self._next

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Conn:
            def __init__(self):
                self.cur = Cur()
                self.committed = False

            def cursor(self):
                return self.cur

            def commit(self):
                self.committed = True

        conn = Conn()
        seed_categories.seed_for_user(conn, 5)
        inserted = [s for s in conn.cur.sql if s.startswith("INSERT")]
        names = {ph for ph in inserted}
        self.assertTrue(conn.committed)
        self.assertEqual(len(inserted), sum(1 + len(ch) for _s, n, _k, _c, _i, ch in seed_categories.DEFAULT_TAXONOMY) - 1)
        self.assertTrue(all("INSERT INTO categories" in s for s in names))


if __name__ == "__main__":
    unittest.main()


class CreateInheritsParentKindTest(unittest.TestCase):
    PARENT = {**CAT, "id": 9, "name": "Income", "slug": "income", "kind": "income", "color": "c5", "icon": "wallet"}

    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(categories_api.bp)
        self.q = _stubs.Router([("FROM categories WHERE id = %s AND user_id", self.PARENT),
                                ("COALESCE(MAX(sort_order)", {"n": 0}),
                                ("FROM categories WHERE id = %s", {"id": 10, "kind": "income"})])
        self.x = _stubs.Router(default={"id": 10})

    def _post(self, body):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(categories_api, "db", FAKE):
            return c.post("/api/categories", data=json.dumps(body), content_type="application/json")

    def test_child_takes_the_parents_kind_even_when_another_is_sent(self):
        res = self._post({"name": "Bonus", "parent_id": 9, "kind": "expense"})
        self.assertEqual(res.status_code, 201, res.get_json())
        (_sql, params), = self.x.sql("INSERT INTO categories")
        self.assertEqual(params[4], "income")

    def test_non_integer_parent_is_400(self):
        res = self._post({"name": "Bonus", "parent_id": "abc"})
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"Parent category must be an integer", res.get_data())

    def test_icon_names_are_plain_identifiers(self):
        self.assertEqual(self._post({"name": "X", "icon": "__proto__"}).get_json(), {"error": "Invalid icon name"})
        self.assertEqual(self._post({"name": "X", "icon": "<img>"}).get_json(), {"error": "Invalid icon name"})
        self.assertEqual(self._post({"name": "X", "icon": "shopping-cart"}).status_code, 201)


class KindChangeSyncsTransactionsTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(categories_api.bp)
        self.x = _stubs.Router(default=1)

    def _put(self, cat, body):
        q = _stubs.Router([("FROM categories WHERE id = %s AND user_id", cat),
                           ("SELECT id FROM categories WHERE parent_id", [{"id": 31}])])
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
        with mock.patch.object(FAKE, "query", side_effect=q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(categories_api, "db", FAKE):
            return c.put(f"/api/categories/{cat['id']}", data=json.dumps(body), content_type="application/json")

    def test_becoming_a_transfer_flags_confirmed_rows_of_the_tree(self):
        self.assertEqual(self._put(CAT, {"kind": "transfer"}).status_code, 200)
        (_sql, params), = self.x.sql("UPDATE transactions SET is_transfer = TRUE")
        self.assertEqual(params, ([3, 31],))
        self.assertEqual([p for _s, p in self.x.sql("UPDATE categories SET kind")], [("transfer", 3)])

    def test_leaving_transfer_unflags_unpaired_rows(self):
        self.assertEqual(self._put({**CAT, "kind": "transfer"}, {"kind": "expense"}).status_code, 200)
        (_sql, params), = self.x.sql("UPDATE transactions SET is_transfer = FALSE")
        self.assertEqual(params, ([3, 31],))

    def test_unchanged_kind_touches_no_transactions(self):
        self.assertEqual(self._put(CAT, {"name": "Food"}).status_code, 200)
        self.assertEqual(self.x.sql("UPDATE transactions"), [])


class TransferCategoriesAreProtectedTest(unittest.TestCase):
    def test_deleting_a_system_transfer_category_is_refused(self):
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(categories_api.bp)
        q = _stubs.Router([("FROM categories WHERE id = %s AND user_id", {**CAT, "id": 2, "slug": "transfers", "kind": "transfer", "is_system": True})])
        x = _stubs.Router(default=1)
        c = app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
        with mock.patch.object(FAKE, "query", side_effect=q), mock.patch.object(FAKE, "execute", side_effect=x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(categories_api, "db", FAKE):
            res = c.delete("/api/categories/2")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(x.sql("DELETE FROM categories"), [])
