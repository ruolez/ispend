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
        self.q.routes.append(("FROM users WHERE id", {"role": "admin", "is_active": True}))
        c = self._client(role="user")
        self.assertEqual(self._get(c, "/admin-only").status_code, 200)
        with c.session_transaction() as s:
            self.assertEqual(s["role"], "admin")

    def test_deactivated_user_loses_the_session(self):
        self.q.routes.append(("FROM users WHERE id", {"role": "user", "is_active": False}))
        c = self._client()
        res = self._get(c, "/protected")
        self.assertEqual((res.status_code, res.get_json()), (401, {"error": "Not authenticated"}))
        with c.session_transaction() as s:
            self.assertNotIn("user_id", s)

    def test_deleted_user_loses_the_session(self):
        c = self._client()
        self.assertEqual(self._get(c, "/protected").status_code, 401)

    def test_demoted_admin_gets_403_on_admin_routes(self):
        self.q.routes.append(("FROM users WHERE id", {"role": "user", "is_active": True}))
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
        user = {"id": 3, "username": "amy", "role": "user", "is_active": True, "password_hash": HASH, "preferences": {}}
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

    def test_inactive_user_cannot_log_in_even_with_the_right_password(self):
        user = {"id": 3, "username": "amy", "role": "user", "is_active": False, "password_hash": HASH, "preferences": {}}
        self.q.routes.append(("FROM users WHERE username", user))
        c, res = self._login({"username": "amy", "password": "correct-horse-battery"})
        self.assertEqual(res.status_code, 401)
        with c.session_transaction() as s:
            self.assertNotIn("user_id", s)

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
        q = _stubs.Router([("FROM users WHERE id", {"role": "user", "is_active": True, "password_hash": HASH})])
        c = app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 3
            s["role"] = "user"
        with mock.patch.object(FAKE, "query", side_effect=q), mock.patch.object(util, "db", FAKE), \
                mock.patch.object(auth, "db", FAKE):
            res = c.put("/api/auth/me/password", data=json.dumps({"current_password": "correct-horse-battery", "password": "short"}),
                        content_type="application/json")
        self.assertEqual(res.get_json(), {"error": f"New password must be at least {auth.MIN_PASSWORD_LEN} characters"})


if __name__ == "__main__":
    unittest.main()
