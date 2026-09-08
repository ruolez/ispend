import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask, jsonify  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import auth  # noqa: E402
import user_state  # noqa: E402
import util  # noqa: E402

HASH = generate_password_hash("correct-horse-battery")


def build_app():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(auth.bp)
    app.before_request(auth.refresh_session_user)

    @app.get("/protected")
    @auth.login_required
    def protected():
        return jsonify({"ok": True})

    @app.get("/admin-only")
    @auth.admin_required
    def admin_only():
        return jsonify({"ok": True})

    return app


class SessionEnforcementTest(unittest.TestCase):
    def setUp(self):
        self.app = build_app()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _client(self, uid=7, role="user"):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["role"] = role
        return c

    def _get(self, client, path):
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(auth, "db", FAKE):
            return client.get(path)

    def test_active_user_passes_and_role_is_refreshed_from_the_row(self):
        self.q.routes.append(("FROM users WHERE id", {"role": "admin", "status": "active"}))
        c = self._client(role="user")
        self.assertEqual(self._get(c, "/admin-only").status_code, 200)
        with c.session_transaction() as s:
            self.assertEqual(s["role"], "admin")

    def test_locked_or_deleted_user_loses_the_session(self):
        for status in ("locked", "deleted"):
            with self.subTest(status=status):
                self.q = _stubs.Router([("FROM users WHERE id", {"role": "user", "status": status})])
                c = self._client()
                res = self._get(c, "/protected")
                self.assertEqual((res.status_code, res.get_json()), (401, {"error": "Not authenticated"}))
                with c.session_transaction() as s:
                    self.assertNotIn("user_id", s)

    def test_deleted_user_loses_the_session(self):
        c = self._client()
        self.assertEqual(self._get(c, "/protected").status_code, 401)

    def test_demoted_admin_gets_403_on_admin_routes(self):
        self.q.routes.append(("FROM users WHERE id", {"role": "user", "status": "active"}))
        c = self._client(role="admin")
        res = self._get(c, "/admin-only")
        self.assertEqual((res.status_code, res.get_json()), (403, {"error": "Admin access required"}))
        self.assertEqual(self._get(c, "/protected").status_code, 200)

    def test_anonymous_request_does_not_query_the_database(self):
        res = self._get(self.app.test_client(), "/protected")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(self.q.calls, [])


class LoginTest(unittest.TestCase):
    def setUp(self):
        self.app = build_app()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _login(self, body, client=None):
        c = client or self.app.test_client()
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(auth, "db", FAKE):
            return c, c.post("/api/auth/login", data=json.dumps(body), content_type="application/json")

    def test_success_rotates_the_session(self):
        user = {"id": 3, "username": "amy", "role": "user", "status": "active", "password_hash": HASH, "preferences": {}}
        self.q.routes.append(("FROM users WHERE username", user))
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 99
            s["stale"] = "x"
        c, res = self._login({"username": "amy", "password": "correct-horse-battery"}, c)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"id": 3, "username": "amy", "role": "user", "preferences": {}})
        with c.session_transaction() as s:
            self.assertEqual((s["user_id"], s.get("stale")), (3, None))

    def test_unknown_user_still_runs_a_password_check(self):
        with mock.patch.object(auth, "check_password_hash", return_value=False) as chk:
            _, res = self._login({"username": "ghost", "password": "whatever"})
        self.assertEqual((res.status_code, res.get_json()), (401, {"error": "Invalid username or password"}))
        self.assertEqual(chk.call_count, 1)

    def test_locked_and_deleted_read_identically_to_a_password_holder(self):
        """Someone who already proved the password learns the account is blocked, but never
        whether it was locked or deleted."""
        for status in ("locked", "deleted"):
            with self.subTest(status=status):
                user = {"id": 3, "username": "amy", "role": "user", "status": status,
                        "password_hash": HASH, "preferences": {}}
                self.q = _stubs.Router([("FROM users WHERE username", user)])
                c, res = self._login({"username": "amy", "password": "correct-horse-battery"})
                self.assertEqual((res.status_code, res.get_json()),
                                 (403, {"error": user_state.BLOCKED_MESSAGE}))
                with c.session_transaction() as s:
                    self.assertNotIn("user_id", s)

    def test_a_wrong_password_is_indistinguishable_in_every_state(self):
        for status in ("active", "locked", "deleted"):
            with self.subTest(status=status):
                user = {"id": 3, "username": "amy", "role": "user", "status": status,
                        "password_hash": HASH, "preferences": {}}
                self.q = _stubs.Router([("FROM users WHERE username", user)])
                _, res = self._login({"username": "amy", "password": "not-the-password"})
                self.assertEqual((res.status_code, res.get_json()),
                                 (401, {"error": "Invalid username or password"}))

    def test_login_stamps_last_login_at(self):
        user = {"id": 3, "username": "amy", "role": "user", "status": "active", "password_hash": HASH, "preferences": {}}
        self.q.routes.append(("FROM users WHERE username", user))
        self._login({"username": "amy", "password": "correct-horse-battery"})
        self.assertEqual([p for _s, p in self.x.sql("SET last_login_at")], [(3,)])

    def test_non_object_body_is_400(self):
        c = self.app.test_client()
        res = c.post("/api/auth/login", data=json.dumps([1, 2]), content_type="application/json")
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"JSON object expected", res.get_data())


class PasswordPolicyTest(unittest.TestCase):
    def test_minimum_length(self):
        self.assertEqual(auth.password_problem("a" * (auth.MIN_PASSWORD_LEN - 1)),
                         f"Password must be at least {auth.MIN_PASSWORD_LEN} characters")
        self.assertEqual(auth.password_problem("a" * auth.MIN_PASSWORD_LEN), None)
        self.assertEqual(auth.password_problem(None, "New password"),
                         f"New password must be at least {auth.MIN_PASSWORD_LEN} characters")

    def test_change_own_password_uses_the_policy(self):
        app = build_app()
        q = _stubs.Router([("FROM users WHERE id", {"role": "user", "status": "active", "password_hash": HASH})])
        c = app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 3
            s["role"] = "user"
        with mock.patch.object(FAKE, "query", side_effect=q), mock.patch.object(FAKE, "execute", side_effect=_stubs.Router(default=1)), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(auth, "db", FAKE):
            res = c.put("/api/auth/me/password", data=json.dumps({"current_password": "correct-horse-battery", "password": "short"}),
                        content_type="application/json")
        self.assertEqual(res.get_json(), {"error": f"New password must be at least {auth.MIN_PASSWORD_LEN} characters"})


if __name__ == "__main__":
    unittest.main()


class PreferenceValidationTest(unittest.TestCase):
    def test_known_keys_are_validated_and_null_clears(self):
        clean = auth.clean_preferences({"theme": "dark", "density": None, "currency": "", "default_account_id": "12",
                                        "week_start": 1, "evil": "x"})
        self.assertEqual(clean, {"theme": "dark", "density": None, "currency": "", "default_account_id": 12, "week_start": 1})

    def test_bad_values_raise(self):
        for body in ({"theme": "x" * 10000}, {"density": "cozy"}, {"currency": "EUR"}, {"theme": 5}):
            with self.assertRaises(ValueError, msg=body):
                auth.clean_preferences(body)


class PreferenceCollectionsTest(unittest.TestCase):
    VIEW = {"id": "v_abc", "name": " Big Amazon ", "query": "?q=amazon&min=100&range=this-year", "pinned": 1}

    def test_saved_views_are_normalised(self):
        clean = auth.clean_preferences({"saved_views": [self.VIEW]})
        self.assertEqual(clean, {"saved_views": [{"id": "v_abc", "name": "Big Amazon", "query": "?q=amazon&min=100&range=this-year",
                                                  "pinned": True, "page": "transactions"}]})

    def test_saved_views_limits_and_rejections(self):
        too_many = [{**self.VIEW, "id": f"v{i}"} for i in range(auth.MAX_SAVED_VIEWS + 1)]
        bad = [
            {"saved_views": too_many}, {"saved_views": [{**self.VIEW, "query": "?open=5"}]},
            {"saved_views": [{**self.VIEW, "id": "Bad Id!"}]}, {"saved_views": [self.VIEW, self.VIEW]},
            {"saved_views": [{**self.VIEW, "name": ""}]}, {"saved_views": [{**self.VIEW, "query": "q=x"}]},
            {"saved_views": [{**self.VIEW, "page": "reports"}]}, {"saved_views": "nope"},
            {"review_skips": [1]}, {"review_skips": ["x" * 121]}, {"onboarding": []},
        ]
        for body in bad:
            with self.assertRaises(ValueError, msg=body):
                auth.clean_preferences(body)

    def test_review_skips_dedupe_and_cap(self):
        keys = [f"k{i}" for i in range(auth.MAX_REVIEW_SKIPS + 5)] + ["k0"]
        clean = auth.clean_preferences({"review_skips": keys})
        self.assertEqual(len(clean["review_skips"]), auth.MAX_REVIEW_SKIPS)
        self.assertEqual(clean["review_skips"][:2], ["k0", "k1"])

    def test_onboarding_keeps_known_booleans_only(self):
        self.assertEqual(auth.clean_preferences({"onboarding": {"dismissed": 1, "reports_opened": None, "x": True}}),
                         {"onboarding": {"dismissed": True, "reports_opened": False}})


class ThemeCookieTest(unittest.TestCase):
    def test_login_sets_a_readable_theme_cookie_from_preferences(self):
        app = build_app()
        user = {"id": 3, "username": "amy", "role": "user", "status": "active", "password_hash": HASH, "preferences": {"theme": "dark"}}
        q = _stubs.Router([("FROM users WHERE username", user)])
        c = app.test_client()
        with mock.patch.object(FAKE, "query", side_effect=q), mock.patch.object(FAKE, "execute", side_effect=_stubs.Router(default=1)), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(auth, "db", FAKE):
            res = c.post("/api/auth/login", data=json.dumps({"username": "amy", "password": "correct-horse-battery"}), content_type="application/json")
        cookie = next(h for h in res.headers.getlist("Set-Cookie") if h.startswith("ispend_theme="))
        self.assertIn("ispend_theme=dark", cookie)
        self.assertNotIn("HttpOnly", cookie)

    def test_logout_clears_the_theme_cookie(self):
        app = build_app()
        res = app.test_client().post("/api/auth/logout")
        cookie = next(h for h in res.headers.getlist("Set-Cookie") if h.startswith("ispend_theme="))
        self.assertIn("Max-Age=0", cookie)
