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
import seed_categories  # noqa: E402
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
                                "ai_insights_enabled": "", "shared_available": True, "shared_model": "m/shared", "is_admin": False})
        admin = self._call("get", "/api/settings", uid=1, role="admin").get_json()
        self.assertEqual((admin["is_admin"], admin["shared_api_key"], admin["openrouter_api_key"]), (True, MASK, ""))

    def test_put_keeps_key_when_mask_is_echoed_and_stores_per_user(self):
        FAKE.settings["u2:openrouter_api_key"] = "sk-me"
        self._call("put", "/api/settings", {"openrouter_api_key": MASK, "openrouter_model": " m/new ",
                                             "ai_categorize_enabled": True, "ignored": "x"})
        self.assertEqual(FAKE.settings, {"u2:openrouter_api_key": "sk-me", "u2:openrouter_model": "m/new",
                                         "u2:ai_categorize_enabled": "1"})
        self._call("put", "/api/settings", {"openrouter_api_key": "sk-other"})
        self.assertEqual(FAKE.settings["u2:openrouter_api_key"], "sk-other")

    def test_only_admins_can_share_or_clear_the_global_key(self):
        self._call("put", "/api/settings", {"openrouter_api_key": "sk-u", "openrouter_model": "m", "shared": True})
        self.assertNotIn("openrouter_api_key", FAKE.settings)
        self._call("put", "/api/settings", {"openrouter_api_key": "sk-a", "openrouter_model": "m", "shared": True}, uid=1, role="admin")
        self.assertEqual((FAKE.settings["openrouter_api_key"], FAKE.settings["openrouter_model"]), ("sk-a", "m"))
        self._call("put", "/api/settings", {"clear_shared": True}, uid=1, role="admin")
        self.assertEqual((FAKE.settings["openrouter_api_key"], FAKE.settings["openrouter_model"]), ("", ""))
        self.assertEqual(FAKE.settings["u1:openrouter_api_key"], "sk-a")

    def test_client_settings_reflect_the_current_user(self):
        FAKE.settings.update({"u2:openrouter_api_key": "k", "u2:openrouter_model": "m", "u2:ai_categorize_enabled": "1"})
        body = self._call("get", "/api/settings/client").get_json()
        self.assertEqual((body["ai_configured"], body["ai_categorize_enabled"], body["ai_insights_enabled"], body["ai_model"]),
                         (True, True, False, "m"))
        other = self._call("get", "/api/settings/client", uid=3).get_json()
        self.assertFalse(other["ai_configured"])

    def test_users_endpoints_are_admin_only(self):
        self.assertEqual(self._call("get", "/api/users").status_code, 403)
        self.assertEqual(self._call("post", "/api/users", {"username": "x", "password": "secret1"}).status_code, 403)

    def test_create_user_seeds_categories(self):
        self.x.routes.append(("INSERT INTO users", {"id": 7}))
        with mock.patch.object(seed_categories, "seed_for_user") as seed:
            res = self._call("post", "/api/users", {"username": "eve", "password": "secret1", "role": "user"}, uid=1, role="admin")
        self.assertEqual((res.status_code, res.get_json()), (201, {"id": 7}))
        self.assertEqual(seed.call_args.args, (FAKE, 7))
        self.assertEqual(self.x.sql("INSERT INTO users")[0][1][0], "eve")

    def test_create_user_validation_and_duplicates(self):
        res = self._call("post", "/api/users", {"username": "eve", "password": "abc"}, uid=1, role="admin")
        self.assertEqual(res.status_code, 400)
        self.q.routes.append(("SELECT id FROM users WHERE username", {"id": 3}))
        res = self._call("post", "/api/users", {"username": "eve", "password": "secret1"}, uid=1, role="admin")
        self.assertEqual(res.get_json()["error"], "Username already exists")

    def test_admin_cannot_deactivate_demote_or_delete_self(self):
        self.q.routes.append(("SELECT id FROM users WHERE id", {"id": 1}))
        res = self._call("put", "/api/users/1", {"is_active": False}, uid=1, role="admin")
        self.assertEqual(res.get_json()["error"], "You cannot deactivate your own account")
        res = self._call("put", "/api/users/1", {"role": "user"}, uid=1, role="admin")
        self.assertEqual(res.get_json()["error"], "You cannot remove your own admin role")
        res = self._call("delete", "/api/users/1", uid=1, role="admin")
        self.assertEqual(res.get_json()["error"], "You cannot delete your own account")
        self.assertEqual([c for c in self.x.calls if c[0].startswith("UPDATE users") or c[0].startswith("DELETE")], [])

    def test_deactivate_other_user(self):
        res = self._call("delete", "/api/users/5", uid=1, role="admin")
        self.assertEqual(res.status_code, 200)
        self.assertEqual([p for _s, p in self.x.sql("UPDATE users SET is_active = FALSE")], [(5,)])


if __name__ == "__main__":
    unittest.main()
