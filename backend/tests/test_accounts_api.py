import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import accounts_api  # noqa: E402
import util  # noqa: E402



class AccountsApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(accounts_api.bp)
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _client(self, uid=1):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["role"] = "user"
        return c

    def _call(self, method, path, body=None):
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(accounts_api, "db", FAKE):
            return getattr(self._client(), method)(path, data=json.dumps(body) if body is not None else None,
                                                   content_type="application/json")

    def test_validation_errors(self):
        for body, message in (({}, "Account name is required"),
                              ({"name": "Card", "currency": "EUR"}, "Currency must be USD or CAD"),
                              ({"name": "Card", "account_type": "crypto"}, "Invalid account type")):
            res = self._call("post", "/api/accounts", body)
            self.assertEqual((res.status_code, res.get_json()["error"]), (400, message), body)
        self.assertEqual(self.x.calls, [])

    def test_duplicate_name_is_refused(self):
        self.q.routes.append(("lower(name) = lower(%s)", {"id": 5}))
        res = self._call("post", "/api/accounts", {"name": "Chase"})
        self.assertEqual((res.status_code, res.get_json()["error"]), (400, "An account with that name already exists"))

    def test_create_normalizes_and_scopes_to_user(self):
        self.x.routes.append(("INSERT INTO accounts", {"id": 9}))
        res = self._call("post", "/api/accounts", {"name": " Amex Gold ", "currency": "cad", "last4": "**** 1234",
                                                   "account_type": "credit_card", "institution": "amex"})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json(), {"id": 9, "name": "Amex Gold", "account_type": "credit_card", "currency": "CAD",
                                          "last4": "1234", "color": "c1", "institution": "amex"})
        sql, params = self.x.sql("INSERT INTO accounts")[0]
        self.assertEqual(params, (1, "Amex Gold", "amex", "credit_card", "CAD", "1234", "c1"))
        self.assertEqual(self.x.sql("INSERT INTO audit_log")[0][1][:2], (1, "account.create"))

    def test_update_and_delete_of_foreign_account_are_404(self):
        res = self._call("put", "/api/accounts/77", {"name": "x"})
        self.assertEqual(res.status_code, 404)
        res = self._call("delete", "/api/accounts/77")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.q.calls[0][1], (77, 1))

    def test_update_changes_currency_on_transactions_too(self):
        self.q.routes.append(("SELECT * FROM accounts WHERE id", {"id": 4, "name": "Chase", "account_type": "checking",
                                                                 "currency": "USD", "last4": None, "color": "c2",
                                                                 "institution": None, "is_active": True}))
        res = self._call("put", "/api/accounts/4", {"currency": "CAD"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p for _s, p in self.x.sql("UPDATE transactions SET currency")], [("CAD", 4)])
        upd = self.x.sql("UPDATE accounts SET")[0][1]
        self.assertEqual(upd, ("Chase", None, "checking", "CAD", None, "c2", True, 4))

    def test_delete_with_transactions_needs_force(self):
        self.q.routes += [("SELECT id FROM accounts WHERE id", {"id": 4}), ("COUNT(*) AS n FROM transactions", {"n": 12})]
        res = self._call("delete", "/api/accounts/4")
        self.assertEqual(res.status_code, 409)
        self.assertIn("12 transactions", res.get_json()["error"])
        self.assertEqual(self.x.sql("DELETE FROM accounts"), [])

    def test_force_delete_discards_statements_and_files(self):
        self.q.routes += [("SELECT id FROM accounts WHERE id", {"id": 4}), ("COUNT(*) AS n FROM transactions", {"n": 12}),
                          ("FROM statements WHERE account_id", [{"id": 30}, {"id": 31}])]
        import importer
        with mock.patch.object(importer, "discard_statement", create=True) as discard:
            res = self._call("delete", "/api/accounts/4?force=true")
        self.assertEqual(res.get_json(), {"ok": True, "deleted_transactions": 12, "deleted_statements": 2})
        self.assertEqual([c.args[0]["id"] for c in discard.call_args_list], [30, 31])
        self.assertEqual([p for _s, p in self.x.sql("DELETE FROM accounts")], [(4,)])

    def test_list_hides_archived_unless_asked(self):
        for path, expect_active in (("/api/accounts", True), ("/api/accounts?all=1", False)):
            self.q.calls.clear()
            self._call("get", path)
            self.assertEqual("AND a.is_active" in self.q.calls[0][0], expect_active, path)
            self.assertEqual(self.q.calls[0][1], (1,))


if __name__ == "__main__":
    unittest.main()
