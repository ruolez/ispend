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
from werkzeug.security import generate_password_hash  # noqa: E402

import auth  # noqa: E402
import seed_categories  # noqa: E402
import util  # noqa: E402

HASH = generate_password_hash("correct-horse-battery")


class _Base(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(auth.bp)
        FAKE.settings.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)
        self.sent = []

    def _call(self, method, path, body=None, uid=None):
        c = self.app.test_client()
        if uid:
            with c.session_transaction() as s:
                s["user_id"] = uid
                s["role"] = "user"
        with mock.patch.object(FAKE, "query", side_effect=self.q), \
                mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(auth, "db", FAKE), \
                mock.patch("mailer.send_async", side_effect=lambda t, to, **k: self.sent.append((t, to, k))):
            return c, getattr(c, method)(path, data=json.dumps(body) if body is not None else None,
                                         content_type="application/json")


class SignupTest(_Base):
    def _enable(self):
        FAKE.settings["signup_enabled"] = "1"

    def test_signup_is_off_by_default(self):
        """An existing self-hosted install must not silently become a public SaaS on upgrade."""
        _, res = self._call("post", "/api/auth/signup", {"email": "a@b.co", "password": "long-enough-1"})
        self.assertEqual((res.status_code, res.get_json()["code"]), (403, "signup_disabled"))
        self.assertEqual(self.x.sql("INSERT INTO users"), [])

    def test_email_and_password_are_validated(self):
        self._enable()
        for body, fragment in (({"email": "nope", "password": "long-enough-1"}, "valid email"),
                               ({"email": "a@b.co", "password": "short"}, "10 characters"),
                               ({"email": "", "password": "long-enough-1"}, "valid email")):
            with self.subTest(body=body):
                _, res = self._call("post", "/api/auth/signup", body)
                self.assertEqual(res.status_code, 400)
                self.assertIn(fragment, res.get_json()["error"])

    def test_a_taken_identity_is_refused(self):
        self._enable()
        self.q.routes.append(("username = %(v)s OR lower(email)", {"id": 5}))
        _, res = self._call("post", "/api/auth/signup", {"email": "a@b.co", "password": "long-enough-1"})
        self.assertEqual((res.status_code, res.get_json()["code"]), (409, "email_taken"))

    def test_signup_creates_a_trial_seeds_categories_and_signs_in(self):
        self._enable()
        FAKE.settings["billing_trial_days"] = "21"
        user = {"id": 9, "username": "a@b.co", "email": "a@b.co", "role": "user", "preferences": {}}
        self.x.routes.append(("INSERT INTO users", user))
        self.q.routes.append(("FROM users u LEFT JOIN subscriptions", {**user, "sub_status": "trialing"}))
        with mock.patch.object(seed_categories, "seed_for_user") as seed:
            c, res = self._call("post", "/api/auth/signup", {"email": "A@B.co", "password": "long-enough-1"})
        self.assertEqual(res.status_code, 201)
        # lower-cased at the boundary so the partial unique index and _by_identity agree
        self.assertEqual(self.x.sql("INSERT INTO users")[0][1][0], "a@b.co")
        self.assertEqual(self.x.sql("INSERT INTO subscriptions")[0][1], (9, 21))
        self.assertEqual(seed.call_args.args, (FAKE, 9))
        with c.session_transaction() as s:
            self.assertEqual(s["user_id"], 9)
        self.assertEqual(self.sent[0][0], "welcome")
        self.assertIn("verify.html?token=", self.sent[0][2]["link"])


class TokenTest(_Base):
    def test_forgot_always_returns_ok_and_stores_only_a_hash(self):
        self.q.routes.append(("FROM users WHERE lower(email)", {"id": 4, "username": "amy",
                                                                "email": "a@b.co"}))
        self.q.routes.append(("COUNT(*) AS n FROM auth_tokens", {"n": 0}))
        _, res = self._call("post", "/api/auth/password/forgot", {"email": "a@b.co"})
        self.assertEqual(res.get_json(), {"ok": True})
        stored = self.x.sql("INSERT INTO auth_tokens")[0][1][2]
        link = self.sent[0][2]["link"]
        self.assertEqual(len(stored), 64)                      # sha256 hex
        self.assertNotIn(stored, link)                         # the plaintext is never stored

    def test_forgot_is_silent_for_an_unknown_address(self):
        _, res = self._call("post", "/api/auth/password/forgot", {"email": "ghost@b.co"})
        self.assertEqual(res.get_json(), {"ok": True})
        self.assertEqual(self.sent, [])
        self.assertEqual(self.x.sql("INSERT INTO auth_tokens"), [])

    def test_forgot_stops_after_the_hourly_cap(self):
        self.q.routes.append(("FROM users WHERE lower(email)", {"id": 4, "username": "amy",
                                                                "email": "a@b.co"}))
        self.q.routes.append(("COUNT(*) AS n FROM auth_tokens", {"n": auth.MAX_RESET_TOKENS_PER_HOUR}))
        _, res = self._call("post", "/api/auth/password/forgot", {"email": "a@b.co"})
        self.assertEqual(res.get_json(), {"ok": True})
        self.assertEqual(self.sent, [])

    def test_reset_consumes_the_token_atomically(self):
        self.x.routes.append(("UPDATE auth_tokens SET used_at", {"user_id": 4}))
        self.x.routes.append(("UPDATE users SET password_hash", {
            "id": 4, "username": "amy", "role": "user", "status": "active",
            "session_epoch": 3, "preferences": {}}))
        _, res = self._call("post", "/api/auth/password/reset",
                            {"token": "t" * 43, "password": "brand-new-password"})
        self.assertEqual(res.status_code, 200)
        sql = self.x.sql("UPDATE auth_tokens SET used_at")[0][0]
        self.assertIn("used_at IS NULL", sql)
        self.assertIn("expires_at > now()", sql)
        # every other session dies with the password
        self.assertIn("session_epoch = session_epoch + 1", self.x.sql("UPDATE users SET password_hash")[0][0])

    def test_an_expired_or_used_token_is_refused(self):
        self.x.routes.append(("UPDATE auth_tokens SET used_at", None))
        _, res = self._call("post", "/api/auth/password/reset",
                            {"token": "t" * 43, "password": "brand-new-password"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("expired or was already used", res.get_json()["error"])
        self.assertEqual(self.x.sql("UPDATE users SET password_hash"), [])

    def test_reset_checks_the_password_policy_before_burning_the_token(self):
        _, res = self._call("post", "/api/auth/password/reset", {"token": "t" * 43, "password": "short"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.x.sql("UPDATE auth_tokens SET used_at"), [])

    def test_a_verify_token_cannot_reset_a_password(self):
        """kind is part of the WHERE clause, so a verification link cannot redeem a reset."""
        self.x.routes.append(("UPDATE auth_tokens SET used_at", None))
        res = self._call("post", "/api/auth/password/reset",
                         {"token": "t" * 43, "password": "brand-new-password"})[1]
        self.assertEqual(res.status_code, 400)
        sql, params = self.x.sql("UPDATE auth_tokens SET used_at")[0]
        self.assertIn("AND kind = %s", sql)
        self.assertEqual(params[1], "reset")

    def test_email_change_requires_the_current_password(self):
        self.q.routes.append(("FROM users u LEFT JOIN subscriptions", {"id": 4, "role": "user",
                                                                       "status": "active"}))
        self.q.routes.append(("id, username, password_hash FROM users", {"id": 4, "username": "amy",
                                                                         "password_hash": HASH}))
        _, res = self._call("put", "/api/auth/me/email",
                            {"email": "new@b.co", "current_password": "wrong"}, uid=4)
        self.assertEqual(res.get_json()["error"], "Current password is incorrect")
        self.assertEqual(self.x.sql("UPDATE users SET email"), [])


class IdentityTest(_Base):
    def test_username_wins_over_email(self):
        self.q.routes.append(("FROM users WHERE username", {"id": 1, "username": "amy",
                                                            "status": "active", "role": "user",
                                                            "password_hash": HASH, "preferences": {}}))
        _, res = self._call("post", "/api/auth/login", {"username": "amy",
                                                        "password": "correct-horse-battery"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(self.q.sql("FROM users WHERE lower(email)")), 0)

    def test_email_is_only_tried_when_the_input_looks_like_one(self):
        self._call("post", "/api/auth/login", {"username": "amy", "password": "x"})
        self.assertEqual(len(self.q.sql("FROM users WHERE lower(email)")), 0)
        self.q.calls.clear()
        self._call("post", "/api/auth/login", {"username": "amy@example.com", "password": "x"})
        self.assertEqual(len(self.q.sql("FROM users WHERE lower(email)")), 1)


if __name__ == "__main__":
    unittest.main()
