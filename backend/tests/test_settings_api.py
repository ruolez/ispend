import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import openrouter  # noqa: E402
import settings_api  # noqa: E402
import util  # noqa: E402

MASK = settings_api.MASK


class SettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(settings_api.bp)
        FAKE.settings.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None, uid=2, role="user"):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["role"] = role
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(settings_api, "db", FAKE), mock.patch.object(openrouter, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None, content_type="application/json")

    def test_get_masks_own_key_and_reports_shared_state(self):
        FAKE.settings.update({"u2:openrouter_api_key": "sk-me", "u2:openrouter_model": "m/mine",
                              "openrouter_api_key": "sk-shared", "openrouter_model": "m/shared"})
        body = self._call("get", "/api/settings").get_json()
        self.assertEqual(body, {"openrouter_api_key": MASK, "openrouter_model": "m/mine", "ai_categorize_enabled": "",
                                "ai_insights_enabled": "", "shared_available": True, "shared_model": "m/shared"})

    def test_put_keeps_key_when_mask_is_echoed_and_stores_per_user(self):
        FAKE.settings["u2:openrouter_api_key"] = "sk-me"
        self._call("put", "/api/settings", {"openrouter_api_key": MASK, "openrouter_model": " m/new ",
                                             "ai_categorize_enabled": True, "ignored": "x"})
        self.assertEqual(FAKE.settings, {"u2:openrouter_api_key": "sk-me", "u2:openrouter_model": "m/new",
                                         "u2:ai_categorize_enabled": "1"})
        self._call("put", "/api/settings", {"openrouter_api_key": "sk-other"})
        self.assertEqual(FAKE.settings["u2:openrouter_api_key"], "sk-other")

    def test_the_instance_key_cannot_be_set_from_the_app(self):
        """The instance key lives in the admin console; the app's settings only ever touch your own."""
        self._call("put", "/api/settings", {"openrouter_api_key": "sk-u", "openrouter_model": "m",
                                             "shared": True, "clear_shared": True})
        self.assertEqual(FAKE.settings, {"u2:openrouter_api_key": "sk-u", "u2:openrouter_model": "m"})

    def test_client_settings_reflect_the_current_user(self):
        FAKE.settings.update({"u2:openrouter_api_key": "k", "u2:openrouter_model": "m", "u2:ai_categorize_enabled": "1"})
        body = self._call("get", "/api/settings/client").get_json()
        self.assertEqual((body["ai_configured"], body["ai_categorize_enabled"], body["ai_insights_enabled"], body["ai_model"]),
                         (True, True, False, "m"))
        other = self._call("get", "/api/settings/client", uid=3).get_json()
        self.assertFalse(other["ai_configured"])


if __name__ == "__main__":
    unittest.main()
