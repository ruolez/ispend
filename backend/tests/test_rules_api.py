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

import rules_api  # noqa: E402
import util  # noqa: E402



class RulesApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(rules_api.bp)
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(rules_api, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None, content_type="application/json")

    def test_validation_errors(self):
        cases = [({"pattern": "(", "match_type": "regex", "category_id": 2}, "Invalid regular expression"),
                 ({"pattern": "NETFLIX"}, "Choose a category, or mark the rule as transfer/excluded"),
                 ({"pattern": "", "category_id": 2}, "Pattern is required"),
                 ({"pattern": "X", "category_id": 2, "match_type": "fuzzy"}, "Match type must be one of"),
                 ({"pattern": "X", "category_id": 2, "amount_min": 10, "amount_max": 5}, "Minimum amount must not exceed maximum amount")]
        for body, msg in cases:
            res = self._call("post", "/api/rules", body)
            self.assertEqual(res.status_code, 400, body)
            self.assertIn(msg, res.get_json()["error"])
        self.assertEqual(self.x.calls, [])

    def test_category_must_belong_to_user(self):
        res = self._call("post", "/api/rules", {"pattern": "NETFLIX", "category_id": 2})
        self.assertEqual((res.status_code, res.get_json()["error"]), (400, "Category not found"))
        self.assertEqual(self.q.calls[0][1], (2, 1))

    def test_create_appends_after_the_last_priority(self):
        self.q.routes += [("SELECT 1 FROM categories", {"1": 1}), ("MAX(priority)", {"p": 30})]
        self.x.routes.append(("INSERT INTO rules", {"id": 5, "user_id": 1, "pattern": "NETFLIX"}))
        res = self._call("post", "/api/rules", {"pattern": "netflix ", "category_id": 2, "match_type": "contains"})
        self.assertEqual((res.status_code, res.get_json()), (201, {"id": 5, "applied": 0}))
        sql, params = self.x.sql("INSERT INTO rules")[0]
        self.assertEqual(params[:8], (1, "contains “netflix”", 40, True, "contains", "description_clean", "netflix", False))

    def test_create_can_apply_to_existing_rows(self):
        self.q.routes += [("SELECT 1 FROM categories", {"1": 1}), ("MAX(priority)", {"p": 0})]
        self.x.routes.append(("INSERT INTO rules", {"id": 5}))
        with mock.patch.object(rules_api, "apply_rules", return_value=17) as apply:
            res = self._call("post", "/api/rules", {"pattern": "NETFLIX", "category_id": 2, "apply_existing": True,
                                                     "only_uncategorized": False})
        self.assertEqual(res.get_json(), {"id": 5, "applied": 17})
        self.assertEqual(apply.call_args.kwargs, {"only_uncategorized": False})

    def test_reorder_assigns_priorities_in_tens_scoped_to_user(self):
        res = self._call("put", "/api/rules/reorder", {"ids": [7, 3, "9"]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p for _s, p in self.x.sql("UPDATE rules SET priority")], [(0, 7, 1), (10, 3, 1), (20, 9, 1)])

    def test_preview_counts_matches_and_returns_sample(self):
        txns = [{"id": i, "description_clean": f"NETFLIX {i}" if i % 2 else "SPOTIFY", "description_raw": "",
                 "merchant_key": "X", "amount": Decimal("-1"), "account_id": 1} for i in range(1, 51)]
        self.q.routes.append(("WHERE id = ANY(%s) ORDER BY txn_date DESC",
                              lambda sql, params: [{"id": i, "txn_date": date(2026, 1, 1), "amount": Decimal("-1")} for i in params[0]]))
        with mock.patch.object(rules_api, "_scan", return_value=iter(txns)) as scan:
            res = self._call("post", "/api/rules/preview", {"pattern": "netflix", "match_type": "contains"})
        body = res.get_json()
        self.assertEqual(body["count"], 25)
        self.assertEqual([r["id"] for r in body["sample"]], list(range(1, 41, 2)))  # capped at 20
        self.assertEqual(scan.call_args.args, (1, False))

    def test_foreign_rule_is_404(self):
        for method, path in (("get", "/api/rules/9"), ("put", "/api/rules/9"), ("delete", "/api/rules/9"), ("post", "/api/rules/9/apply")):
            self.assertEqual(self._call(method, path, {}).status_code, 404, path)
            self.assertEqual(self.q.calls[-1][1], (9, 1))

    def test_delete_own_rule(self):
        self.q.routes.append(("SELECT * FROM rules WHERE id", {"id": 9, "user_id": 1}))
        res = self._call("delete", "/api/rules/9")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p for _s, p in self.x.sql("DELETE FROM rules")], [(9,)])


if __name__ == "__main__":
    unittest.main()
