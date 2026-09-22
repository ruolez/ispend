import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()
_stubs.install_stripe()

from flask import Flask  # noqa: E402

import admin_billing  # noqa: E402
import util  # noqa: E402

MASK = admin_billing.MASK


class AdminBillingConfigTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(admin_billing.bp)
        FAKE.settings.clear()
        self.x = _stubs.Router(default=1)

    def tearDown(self):
        FAKE.settings.clear()

    def _call(self, method, path, body=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
            s["role"] = "admin"
        with mock.patch.object(FAKE, "query", side_effect=_stubs.Router()), \
                mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(admin_billing, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None,
                                      content_type="application/json")

    def test_config_reports_the_verification_switch_on_by_default_and_whether_email_works(self):
        res = self._call("get", "/api/admin/billing/config").get_json()
        self.assertEqual((res["signup_require_verification"], res["email_configured"]),
                         ({"value": True, "locked": False}, False))
        FAKE.settings.update({"signup_require_verification": "0", "smtp_host": "smtp.example.com",
                              "smtp_from_email": "noreply@example.com"})
        res = self._call("get", "/api/admin/billing/config").get_json()
        self.assertEqual((res["signup_require_verification"]["value"], res["email_configured"]), (False, True))

    def test_put_config_stores_both_switches_as_flags(self):
        res = self._call("put", "/api/admin/billing/config",
                         {"signup_enabled": True, "signup_require_verification": False})
        self.assertEqual(res.get_json(), {"ok": True})
        self.assertEqual((FAKE.settings["signup_enabled"], FAKE.settings["signup_require_verification"]), ("1", "0"))
        fields = [p[1] for s, p in self.x.calls if "INSERT INTO audit_log" in s]
        self.assertEqual(fields, ["billing.config_update"])

    def test_put_config_never_blanks_a_masked_secret(self):
        FAKE.settings["smtp_password"] = "keep-me"
        self._call("put", "/api/admin/billing/config", {"smtp_password": MASK, "smtp_host": "smtp.example.com"})
        self.assertEqual((FAKE.settings["smtp_password"], FAKE.settings["smtp_host"]),
                         ("keep-me", "smtp.example.com"))


if __name__ == "__main__":
    unittest.main()
