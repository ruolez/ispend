import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402
import import_layouts_api  # noqa: E402

ROWS = [{"id": 3, "header": ["Date", "Amount"], "bank_profile": None, "account_id": 5, "account_name": "Visa",
         "sample_filename": "july.csv", "times_used": 2, "last_used_at": "2026-09-01", "created_at": "2026-08-01"}]


def client(user_id=1):
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(import_layouts_api.bp)
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = user_id
    return c


class ListTest(unittest.TestCase):
    def test_lists_the_session_users_layouts(self):
        router = _stubs.Router([("FROM import_layouts l", ROWS)])
        with mock.patch.object(FAKE, "query", side_effect=router):
            res = client(4).get("/api/import-layouts")
        self.assertEqual((res.status_code, res.get_json()), (200, ROWS))
        self.assertEqual(router.calls[0][1], (4,))

    def test_requires_login(self):
        app = Flask(__name__)
        app.secret_key = "test"
        app.register_blueprint(import_layouts_api.bp)
        self.assertEqual(app.test_client().get("/api/import-layouts").status_code, 401)


class ForgetTest(unittest.TestCase):
    def test_deletes_and_audits(self):
        router = _stubs.Router([("DELETE FROM import_layouts", 1), ("INSERT INTO audit_log", 1)])
        with mock.patch.object(FAKE, "execute", side_effect=router):
            res = client(4).delete("/api/import-layouts/3")
        self.assertEqual((res.status_code, res.get_json()), (200, {"ok": True}))
        self.assertEqual(router.sql("DELETE FROM import_layouts")[0][1], (4, 3))
        self.assertEqual(len(router.sql("INSERT INTO audit_log")), 1)

    def test_scope_layout_forgets_the_columns_for_every_account(self):
        router = _stubs.Router([("DELETE FROM import_layouts", 2), ("INSERT INTO audit_log", 1)])
        with mock.patch.object(FAKE, "execute", side_effect=router):
            res = client(4).delete("/api/import-layouts/3?scope=layout")
        sql, params = router.sql("DELETE FROM import_layouts")[0]
        self.assertEqual((res.status_code, params), (200, (4, 4, 3)))
        self.assertIn("layout_key = (SELECT layout_key FROM import_layouts WHERE user_id = %s AND id = %s)", sql)

    def test_another_users_layout_is_not_found(self):
        router = _stubs.Router([("DELETE FROM import_layouts", 0)])
        with mock.patch.object(FAKE, "execute", side_effect=router):
            res = client(4).delete("/api/import-layouts/3")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(router.sql("INSERT INTO audit_log"), [])


if __name__ == "__main__":
    unittest.main()
