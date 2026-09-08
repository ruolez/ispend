import json
import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402
from werkzeug.exceptions import HTTPException  # noqa: E402

import budgets_api  # noqa: E402
import reports  # noqa: E402
import util  # noqa: E402

CAT = {"id": 3, "name": "Dining", "parent_id": None, "kind": "expense"}
SUB = {"id": 4, "name": "Coffee", "parent_id": 3, "kind": "expense"}
ROW = {"id": 7, "category_id": 3, "month": date(2026, 9, 1), "amount": Decimal("600.00"), "note": None, "created_at": None, "updated_at": None}


class BudgetsApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(budgets_api.bp)
        self.app.register_error_handler(HTTPException, lambda e: ({"error": e.description}, e.code))
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(budgets_api, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None, content_type="application/json")

    def test_validation(self):
        self.q.routes += [("FROM categories WHERE id = %s AND user_id", lambda sql, p: CAT if p[0] == 3 else None)]
        cases = [({}, 400, "category_id is required"), ({"category_id": 3, "amount": 0}, 400, "amount must be greater than zero"),
                 ({"category_id": 3, "amount": "abc"}, 400, "amount must be an amount"), ({"category_id": 3, "amount": 5, "month": "2026-13"}, 400, "month must be YYYY-MM"),
                 ({"category_id": 99, "amount": 5}, 404, "Category not found"), ({"category_id": 3, "amount": 5, "note": "x" * 201}, 400, "note must be at most 200 characters")]
        for body, status, msg in cases:
            res = self._call("post", "/api/budgets", body)
            self.assertEqual((res.status_code, res.get_json()["error"]), (status, msg), body)

    def test_transfer_category_rejected(self):
        self.q.routes += [("FROM categories WHERE id = %s AND user_id", {**CAT, "kind": "transfer"})]
        res = self._call("post", "/api/budgets", {"category_id": 3, "amount": 50})
        self.assertEqual((res.status_code, res.get_json()["error"]), (400, "Transfers cannot be budgeted"))

    def test_parent_child_overlap_rejected(self):
        self.q.routes += [("FROM categories WHERE id = %s AND user_id", SUB), ("b.category_id = %s", {"id": 7, "name": "Dining"})]
        res = self._call("post", "/api/budgets", {"category_id": 4, "amount": 50, "month": "2026-09"})
        self.assertEqual((res.status_code, res.get_json()["error"]), (400, "Budget Dining or its subcategories, not both"))
        self.q.routes = [("FROM categories WHERE id = %s AND user_id", CAT), ("c.parent_id = %s", {"id": 8})]
        res = self._call("post", "/api/budgets", {"category_id": 3, "amount": 50, "month": "2026-09"})
        self.assertEqual(res.status_code, 400)

    def test_upsert_sql_and_status(self):
        self.q.routes += [("FROM categories WHERE id = %s AND user_id", CAT), ("FROM budgets b WHERE b.id", ROW)]
        self.x.routes += [("INSERT INTO budgets", {"id": 7, "inserted": True})]
        res = self._call("post", "/api/budgets", {"category_id": 3, "amount": "600", "month": "2026-09", "note": " lunches "})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json(), {"id": 7, "category_id": 3, "month": "2026-09", "amount": 600.0, "note": None, "created_at": None, "updated_at": None})
        sql, params = self.x.sql("INSERT INTO budgets")[0]
        self.assertIn("ON CONFLICT (user_id, category_id, month) DO UPDATE", sql)
        self.assertEqual(params, (1, 3, date(2026, 9, 1), Decimal("600.00"), "lunches"))
        self.x.routes = [("INSERT INTO budgets", {"id": 7, "inserted": False})]
        self.assertEqual(self._call("post", "/api/budgets", {"category_id": 3, "amount": 650, "month": "2026-09"}).status_code, 200)

    def test_update_and_delete_require_ownership(self):
        self.assertEqual(self._call("put", "/api/budgets/7", {"amount": 10}).status_code, 404)
        self.assertEqual(self._call("delete", "/api/budgets/7").status_code, 404)
        self.q.routes += [("FROM budgets b WHERE b.id", ROW)]
        res = self._call("put", "/api/budgets/7", {"amount": "10.5"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.x.sql("UPDATE budgets")[0][1], (Decimal("10.50"), None, 7, 1))
        self.assertEqual(self._call("delete", "/api/budgets/7").get_json(), {"ok": True})
        self.assertEqual(self.x.sql("DELETE FROM budgets")[0][1], (7, 1))

    def test_copy_counts_inserted_and_skipped(self):
        self.q.routes += [("SELECT category_id, amount, note FROM budgets", [{"category_id": 3, "amount": Decimal("1"), "note": None}, {"category_id": 5, "amount": Decimal("2"), "note": "x"}])]
        self.x.routes += [("ON CONFLICT DO NOTHING RETURNING id", lambda sql, p: {"id": 1} if p[1] == 3 else None)]
        res = self._call("post", "/api/budgets/copy", {"from": "2026-08", "to": "2026-09"})
        self.assertEqual(res.get_json(), {"copied": 1, "skipped": 1})
        self.assertEqual((self._call("post", "/api/budgets/copy", {"from": "2026-08", "to": "2026-08"}).get_json()["error"]), "Pick two different months")
        self.q.routes = [("SELECT category_id, amount, note FROM budgets", [])]
        self.assertEqual(self._call("post", "/api/budgets/copy", {"from": "2026-07", "to": "2026-09"}).status_code, 400)

    def test_progress_composes_by_category_with_accounts(self):
        rows = [{"id": 3, "name": "Dining", "color": "c2", "icon": "utensils", "parent_id": None, "total": 150.0, "count": 4, "pct": 100.0}]
        self.q.routes += [("FROM budgets b WHERE b.user_id = %s AND b.month", [ROW]),
                          ("SELECT id, name, color, icon, parent_id FROM categories", [{"id": 3, "name": "Dining", "color": "c2", "icon": "utensils", "parent_id": None}])]
        with mock.patch.object(reports, "by_category", return_value=rows) as bc, mock.patch.object(reports, "today", return_value=date(2026, 9, 15)):
            res = self._call("get", "/api/budgets/progress?month=2026-09&account_id=1,2")
        self.assertEqual(res.status_code, 200)
        bc.assert_called_once_with(1, date(2026, 9, 1), date(2026, 9, 30), "sub", [1, 2])
        body = res.get_json()
        self.assertEqual((body["month"], body["days_in_month"], body["days_elapsed"], body["elapsed_pct"]), ("2026-09", 30, 15, 50.0))
        self.assertEqual(body["items"][0]["spent"], 150.0)
        self.assertEqual(body["items"][0]["pace_status"], "on_track")
        self.assertEqual(body["totals"]["remaining"], 450.0)


if __name__ == "__main__":
    unittest.main()
