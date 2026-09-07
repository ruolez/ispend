import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import categorizer  # noqa: E402
import merchants_api  # noqa: E402
import util  # noqa: E402


class MerchantsApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(merchants_api.bp)
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(merchants_api, "db", FAKE), mock.patch.object(categorizer, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None, content_type="application/json")

    def test_update_needs_a_category_or_a_name(self):
        res = self._call("put", "/api/merchants/UBER", {})
        self.assertEqual((res.status_code, res.get_json()), (400, {"error": "category_id or display_name is required"}))

    def test_foreign_category_is_404(self):
        res = self._call("put", "/api/merchants/UBER", {"category_id": 99})
        self.assertEqual((res.status_code, res.get_json()), (404, {"error": "Category not found"}))
        self.assertEqual(self.x.calls, [])

    def test_rename_only_updates_memory_without_learning(self):
        res = self._call("put", "/api/merchants/UBER", {"display_name": "Uber rides"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p for _s, p in self.x.sql("UPDATE merchant_memory SET display_name")], [("Uber rides", 1, "UBER")])
        self.assertEqual(self.x.sql("INSERT INTO merchant_memory"), [])

    def test_category_with_apply_existing_recategorizes_rows(self):
        self.q.routes += [("FROM categories WHERE id", {"id": 7}), ("SELECT id FROM transactions WHERE user_id", [{"id": 3}, {"id": 4}])]
        res = self._call("put", "/api/merchants/UBER", {"category_id": 7, "apply_existing": True})
        self.assertEqual((res.status_code, res.get_json()["applied"]), (200, 2))
        self.assertEqual(len(self.x.sql("INSERT INTO merchant_memory")), 1)
        (_sql, params), = self.x.sql("UPDATE transactions SET category_id = %s, category_status = 'confirmed', category_source = 'merchant'")
        self.assertEqual(params, (7, [3, 4]))

    def test_delete_forgets_the_merchant(self):
        res = self._call("delete", "/api/merchants/UBER")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p for _s, p in self.x.sql("DELETE FROM merchant_memory")], [(1, "UBER")])


if __name__ == "__main__":
    unittest.main()
