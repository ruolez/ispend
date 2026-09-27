"""Data rights: export coverage, who may erase, and never erasing someone who is still billed."""
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()
STRIPE = _stubs.install_stripe()

from flask import Flask  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import admin_users  # noqa: E402
import auth  # noqa: E402
import entitlement  # noqa: E402
import privacy  # noqa: E402
from backup import tables  # noqa: E402

HASH = generate_password_hash("correct-horse-battery")


class CoverageTest(unittest.TestCase):
    def test_every_table_is_either_exported_or_excused(self):
        """A new table holding someone's data must be added to the export, or explicitly excused."""
        every = set(tables.TABLE_ORDER) | set(tables.EXCLUDED)
        classified = set(privacy.EXPORTED) | set(privacy.NOT_EXPORTED)
        self.assertEqual(every - classified, set(), "decide whether these belong in a person's export")
        self.assertEqual(classified - every, set(), "these tables do not exist")
        self.assertEqual(set(privacy.EXPORTED) & set(privacy.NOT_EXPORTED), set())

    def test_data_rights_are_never_behind_the_paywall(self):
        for path in ("/api/auth/me/exports", "/api/auth/me"):
            with self.subTest(path=path):
                self.assertTrue(entitlement.write_allowed(path))

    def test_secret_settings_are_masked(self):
        rows = [{"key": "u5:openrouter_api_key", "value": "sk-or-123"}, {"key": "u5:openrouter_model", "value": "m/a"},
                {"key": "u5:empty_token", "value": ""}]
        with mock.patch.object(FAKE, "query", return_value=rows):
            self.assertEqual(privacy._settings(5), {"openrouter_api_key": "(hidden)", "openrouter_model": "m/a",
                                                    "empty_token": ""})


class _Api(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "t"
        self.app.register_blueprint(auth.bp)
        self.app.register_blueprint(admin_users.bp)
        FAKE.settings.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None, uid=7, role="user", stepped_up=True):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"], s["role"] = uid, role
            if stepped_up:
                s["stepup_until"] = time.time() + 600
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x):
            res = getattr(c, method)(path, data=json.dumps(body) if body is not None else None,
                                     content_type="application/json")
        with c.session_transaction() as s:
            self.signed_in = "user_id" in s
        return res


class SelfServiceTest(_Api):
    def test_deleting_your_own_account(self):
        user = {"id": 7, "role": "user", "password_hash": HASH}
        cases = [({"password": "nope", "confirm": "DELETE"}, user, 400, True),
                 ({"password": "correct-horse-battery", "confirm": "delete"}, user, 400, True),
                 ({"password": "correct-horse-battery", "confirm": "DELETE"}, {**user, "role": "admin"}, 409, True),
                 ({"password": "correct-horse-battery", "confirm": "DELETE"}, user, 200, False)]
        for body, row, status, still_in in cases:
            with self.subTest(body=body, role=row["role"]):
                self.q = _stubs.Router([("FROM users WHERE id", row)])
                with mock.patch.object(privacy, "erase", return_value={"id": 3}) as erase:
                    res = self._call("delete", "/api/auth/me", body)
                self.assertEqual((res.status_code, self.signed_in), (status, still_in))
                self.assertEqual(erase.called, status == 200)
                if status == 200:
                    self.assertEqual(erase.call_args.args, (7, "self"))

    def test_export_requests_are_limited(self):
        for counts, status in (({"busy": 1, "today": 1}, 409), ({"busy": 0, "today": 3}, 429), ({"busy": 0, "today": 0}, 202)):
            with self.subTest(counts=counts):
                self.q = _stubs.Router([("FROM data_exports WHERE user_id", counts)])
                with mock.patch.object(privacy, "request_export", return_value=4) as req:
                    res = self._call("post", "/api/auth/me/exports", {})
                self.assertEqual(res.status_code, status)
                self.assertEqual(req.called, status == 202)

    def test_a_download_is_only_yours(self):
        self.q = _stubs.Router([("FROM data_exports WHERE id", None)])
        res = self._call("get", "/api/auth/me/exports/4/download")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.q.sql("FROM data_exports WHERE id")[0][1], (4, 7))


class AdminTest(_Api):
    PERSON = {"id": 5, "username": "eve", "email": "eve@example.com", "status": "active", "role": "user",
              "created_at": None, "email_verified_at": None, "first_commit_at": None}

    def test_erasing_needs_step_up_the_username_and_someone_else(self):
        self.q = _stubs.Router([("FROM users WHERE id = %s", self.PERSON)])
        cases = [(5, {"confirm": "eve"}, False, 403), (1, {"confirm": "admin"}, True, 400),
                 (5, {"confirm": "Eve"}, True, 409), (5, {"confirm": "eve", "reason": "asked by email"}, True, 200)]
        for target, body, stepped, status in cases:
            with self.subTest(target=target, body=body, stepped=stepped):
                with mock.patch.object(privacy, "erase", return_value={"id": 9, "stripe_canceled": False}) as erase:
                    res = self._call("post", f"/api/admin/users/{target}/erase", body, uid=1, role="admin", stepped_up=stepped)
                self.assertEqual(res.status_code, status)
                if status == 200:
                    self.assertEqual(erase.call_args.kwargs, {"admin_id": 1, "reason": "asked by email"})

    def test_an_admin_export_goes_to_the_person(self):
        self.q = _stubs.Router([("FROM users WHERE id = %s", self.PERSON), ("FROM data_exports WHERE user_id", None)])
        with mock.patch.object(privacy, "request_export", return_value=4) as req:
            res = self._call("post", "/api/admin/users/5/export", {}, uid=1, role="admin")
        self.assertEqual((res.status_code, req.call_args.args), (202, (5, 1)))
        self.assertNotIn("file", json.dumps(res.get_json()))


class EraseTest(unittest.TestCase):
    def setUp(self):
        self.q = _stubs.Router([("FROM users WHERE id", {"id": 5, "username": "eve", "email": "e@x.io", "role": "user", "status": "active"}),
                                ("FROM subscriptions WHERE user_id", {"status": "active", "stripe_subscription_id": "sub_1",
                                                                     "stripe_customer_id": "cus_1"}),
                                ("AS transactions", {"transactions": 3, "statements": 1, "accounts": 1})])
        self.x = _stubs.Router([("INSERT INTO erasures", {"id": 2, "created_at": __import__("datetime").datetime(2026, 9, 1)})], default=1)
        FAKE.settings.update({"stripe_secret_key": "sk_test", "stripe_price_monthly": "price_m"})

    def _erase(self):
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch("mailer.send_async"), mock.patch.object(privacy.shutil, "rmtree"):
            return privacy.erase(5, "admin", admin_id=1)

    def test_nothing_is_deleted_when_stripe_will_not_cancel(self):
        STRIPE.Subscription.cancel = mock.Mock(side_effect=RuntimeError("card network down"))
        with self.assertRaises(privacy.EraseError) as ctx, self.assertLogs("privacy", "ERROR"):
            self._erase()
        self.assertEqual(ctx.exception.status, 502)
        self.assertEqual(self.x.sql("DELETE FROM users"), [])

    def test_a_billed_person_is_cancelled_first_then_removed_and_recorded(self):
        STRIPE.Subscription.cancel = mock.Mock()
        STRIPE.Customer.modify = mock.Mock()
        record = self._erase()
        STRIPE.Subscription.cancel.assert_called_once_with("sub_1")
        self.assertEqual(record["stripe_canceled"], True)
        order = [s.split()[0] + " " + s.split()[1] + " " + s.split()[2] for s, _ in self.x.calls]
        self.assertEqual(order[:4], ["UPDATE audit_log SET", "DELETE FROM users", "DELETE FROM settings",
                                     "INSERT INTO erasures"])
        email_hash = self.x.sql("INSERT INTO erasures")[0][1][1]
        self.assertEqual(len(email_hash), 64, "only a one-way hash of the email is kept")


if __name__ == "__main__":
    unittest.main()
