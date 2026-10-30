"""The active profile: resolved per request, switched by the user, named by each tab, and the
preferences that belong to it."""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask, g, jsonify  # noqa: E402

import auth  # noqa: E402
import util  # noqa: E402

UID, PERSONAL, BUSINESS = 7, 70, 71
USER_ROW = {"id": UID, "username": "ann", "email": None, "email_verified_at": None, "role": "user",
            "status": "active", "preferences": {"theme": "dark", "saved_views": [{"id": "old"}]},
            "profile_id": PERSONAL}
PROFILES = [
    {"id": PERSONAL, "name": "Personal", "kind": "personal", "color": "c1",
     "preferences": {"saved_views": [], "default_account_id": 3, "categories_seeded": True}},
    {"id": BUSINESS, "name": "Studio", "kind": "business", "color": "c4", "preferences": {}},
]


def build_app():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(auth.bp)
    app.before_request(auth.refresh_session_user)
    app.before_request(auth.profile_guard)

    @app.route("/api/things", methods=["GET", "POST"])
    @auth.login_required
    def things():
        return jsonify({"profile_id": g.get("profile_id")})

    return app


class ProfileSessionTest(unittest.TestCase):
    def setUp(self):
        self.app = build_app()
        self.q = _stubs.Router([("FROM users u", USER_ROW), ("FROM profiles WHERE user_id", PROFILES)])
        self.x = _stubs.Router(default=1)

    def _client(self, role="user", profile_id=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = UID
            s["role"] = role
            if profile_id is not None:
                s["profile_id"] = profile_id
        return c

    def _send(self, client, method, path, body=None, headers=None):
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(auth, "db", FAKE):
            return getattr(client, method)(path, data=json.dumps(body) if body is not None else None,
                                           content_type="application/json", headers=headers or {})

    def test_the_resolved_profile_is_kept_in_the_session(self):
        c = self._client(profile_id=BUSINESS)
        res = self._send(c, "get", "/api/things")
        self.assertEqual(res.get_json(), {"profile_id": PERSONAL})
        sql, params = self.q.sql("FROM users u")[0]
        self.assertEqual(params, (BUSINESS, UID), "the session's choice is asked for first")
        with c.session_transaction() as s:
            self.assertEqual(s["profile_id"], PERSONAL)

    def test_admin_accounts_get_no_profile(self):
        self.q.routes.insert(0, ("FROM users u", {**USER_ROW, "role": "admin"}))
        res = self._send(self._client(role="admin"), "get", "/api/things")
        self.assertEqual(res.get_json(), {"profile_id": None})

    def test_a_tab_on_another_profile_is_refused_for_reads_and_writes(self):
        for method in ("get", "post"):
            with self.subTest(method=method):
                res = self._send(self._client(), method, "/api/things", {} if method == "post" else None,
                                 headers={"X-Profile": str(BUSINESS)})
                self.assertEqual((res.status_code, res.get_json()["code"], res.get_json()["profile_id"]),
                                 (409, "profile_changed", PERSONAL))

    def test_the_matching_profile_or_no_header_passes(self):
        for headers in ({"X-Profile": str(PERSONAL)}, {}):
            with self.subTest(headers=headers):
                self.assertEqual(self._send(self._client(), "get", "/api/things", headers=headers).status_code, 200)

    def test_a_download_link_names_its_profile_in_the_query(self):
        self.assertEqual(self._send(self._client(), "get", f"/api/things?pid={BUSINESS}").status_code, 409)
        self.assertEqual(self._send(self._client(), "get", f"/api/things?pid={PERSONAL}").status_code, 200)

    def test_account_level_paths_are_not_checked(self):
        res = self._send(self._client(), "get", "/api/auth/me", headers={"X-Profile": str(BUSINESS)})
        self.assertEqual(res.status_code, 200)

    def test_me_lists_profiles_and_merges_the_active_profiles_preferences(self):
        body = self._send(self._client(), "get", "/api/auth/me").get_json()
        self.assertEqual((body["profile"], body["profiles"], body["preferences"]), (
            {"id": PERSONAL, "name": "Personal", "kind": "personal", "color": "c1"},
            [{"id": PERSONAL, "name": "Personal", "kind": "personal", "color": "c1"},
             {"id": BUSINESS, "name": "Studio", "kind": "business", "color": "c4"}],
            {"theme": "dark", "saved_views": [], "default_account_id": 3}))

    def test_switching_checks_ownership_and_remembers_the_choice(self):
        self.q.routes.insert(0, ("FROM profiles WHERE id", None))
        self.assertEqual(self._send(self._client(), "post", "/api/auth/me/profile", {"id": 99}).status_code, 404)
        self.assertEqual(self.q.sql("FROM profiles WHERE id")[0][1], (99, UID))

        self.q.routes.insert(0, ("FROM profiles WHERE id", PROFILES[1]))
        c = self._client()
        res = self._send(c, "post", "/api/auth/me/profile", {"id": BUSINESS})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.x.sql("UPDATE users SET last_profile_id")[0][1], (BUSINESS, UID))
        with c.session_transaction() as s:
            self.assertEqual(s["profile_id"], BUSINESS)

    def test_admins_cannot_switch(self):
        self.q.routes.insert(0, ("FROM users u", {**USER_ROW, "role": "admin"}))
        res = self._send(self._client(role="admin"), "post", "/api/auth/me/profile", {"id": PERSONAL})
        self.assertEqual(res.status_code, 403)

    def test_book_preferences_go_to_the_profile_and_the_rest_to_the_user(self):
        self.q.routes[:0] = [("SELECT preferences FROM users", {"preferences": {"theme": "dark"}}),
                             ("SELECT preferences FROM profiles", {"preferences": {"review_skips": ["a"]}}),
                             ("FROM accounts WHERE id", {"?column?": 1})]
        res = self._send(self._client(), "put", "/api/auth/me/preferences",
                         {"theme": "light", "default_account_id": 4, "saved_views": []})
        self.assertEqual(res.get_json(), {"theme": "light", "review_skips": ["a"], "default_account_id": 4, "saved_views": []})
        self.assertEqual(json.loads(self.x.sql("UPDATE users SET preferences")[0][1][0]), {"theme": "light"})
        self.assertEqual(json.loads(self.x.sql("UPDATE profiles SET preferences")[0][1][0]),
                         {"default_account_id": 4, "saved_views": []})
        self.assertEqual(self.q.sql("FROM accounts WHERE id")[0][1], (4, PERSONAL))

    def test_a_default_account_from_other_books_is_refused(self):
        self.q.routes[:0] = [("FROM accounts WHERE id", None)]
        res = self._send(self._client(), "put", "/api/auth/me/preferences", {"default_account_id": 4})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.x.sql("SET preferences"), [])


if __name__ == "__main__":
    unittest.main()
