"""/api/profiles without a database: validation, ownership and the checks that come before any write.
The writes themselves run against Postgres in test_profiles_integration."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import profiles  # noqa: E402
import profiles_api  # noqa: E402
import util  # noqa: E402

UID, HOME, STUDIO = 3, 30, 31
OWNED = {HOME: {"id": HOME, "name": "Personal", "kind": "personal", "color": "c1", "sort_order": 0},
         STUDIO: {"id": STUDIO, "name": "Studio", "kind": "business", "color": "c4", "sort_order": 1}}


class ProfilesApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(profiles_api.bp)
        self.q = _stubs.Router([("FROM profiles WHERE id = %s AND user_id = %s", lambda sql, p: OWNED.get(p[0]) if p[1] == UID else None),
                                ("lower(name) = lower(%s)", None)])
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None, role="user"):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s.update({"user_id": UID, "role": role, "profile_id": HOME})
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(profiles, "db", FAKE), mock.patch.object(profiles_api, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None, content_type="application/json")

    def test_create_validates_name_kind_and_color(self):
        cases = [({}, "Give the profile a name"), ({"name": "x" * 41}, "A profile name can be at most 40 characters"),
                 ({"name": "Shop", "kind": "charity"}, "A profile is personal or business"),
                 ({"name": "Shop", "color": "red"}, "Invalid color slot")]
        for body, message in cases:
            with self.subTest(body=body):
                res = self._call("post", "/api/profiles", body)
                self.assertEqual((res.status_code, res.get_json()["error"]), (400, message))
        self.assertEqual(self.x.sql("INSERT INTO profiles"), [])

    def test_a_taken_name_is_refused(self):
        self.q.routes.insert(0, ("lower(name) = lower(%s)", {"id": HOME}))
        res = self._call("post", "/api/profiles", {"name": "personal"})
        self.assertEqual((res.status_code, res.get_json()["error"]), (400, "You already have a profile with that name"))

    def test_copying_needs_one_of_ones_own_profiles(self):
        res = self._call("post", "/api/profiles", {"name": "Copy", "start": "copy", "copy_from": 999})
        self.assertEqual(res.status_code, 404)

    def test_admin_accounts_have_no_profiles(self):
        self.assertEqual(self._call("post", "/api/profiles", {"name": "x"}, role="admin").status_code, 403)

    def test_delete_wants_the_name_typed(self):
        res = self._call("delete", f"/api/profiles/{STUDIO}", {"confirm": "studi"})
        self.assertEqual((res.status_code, res.get_json()["error"]), (400, "Type the profile's name to delete it"))
        self.assertEqual(self.x.sql("DELETE FROM profiles"), [])

    def test_someone_elses_profile_is_not_found(self):
        for method, path, body in (("put", "/api/profiles/77", {"name": "x"}), ("delete", "/api/profiles/77", {"confirm": "x"}),
                                   ("post", "/api/profiles/77/move", {"account_ids": [1]})):
            with self.subTest(path=path, method=method):
                self.assertEqual(self._call(method, path, body).status_code, 404)

    def test_a_move_goes_to_another_profile_and_names_accounts(self):
        self.assertEqual(self._call("post", f"/api/profiles/{HOME}/move", {"account_ids": [1]}).status_code, 400)
        res = self._call("post", f"/api/profiles/{STUDIO}/move-preview", {"account_ids": []})
        self.assertEqual((res.status_code, res.get_json()["error"]), (400, "Choose the accounts to move"))
        res = self._call("post", f"/api/profiles/{STUDIO}/move", {"account_ids": [1], "rename": {"x": "y"}})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
