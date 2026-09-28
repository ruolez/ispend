"""Step-up re-authentication, sign-in history, the activity log's new columns and the admin fixes
that came with them."""
import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()
_stubs.install_stripe()

from flask import Flask  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import admin_api  # noqa: E402
import admin_users  # noqa: E402
import admin_backup  # noqa: E402
import admin_billing  # noqa: E402
import auth  # noqa: E402
import mailer  # noqa: E402
import util  # noqa: E402

PASSWORD = "correct-horse-battery"  # noqa: S105 - test fixture
HASH = generate_password_hash(PASSWORD)
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")

# Every request that must re-check the admin's password, with a body that would otherwise pass
# validation far enough to write.
STEP_UP_ROUTES = [
    ("post", "/api/admin/me/leftover-data/wipe", {"confirm": "delete"}),
    ("put", "/api/admin/ai-config", {"api_key": "sk-new"}),
    ("put", "/api/admin/users/5/password", {"password": "a-long-enough-password"}),
    ("delete", "/api/admin/users/5?permanent=true&confirm=eve", None),
    ("get", "/api/admin/audit?format=csv", None),
    ("post", "/api/admin/billing/users/5/cancel", {}),
    ("put", "/api/admin/billing/config", {"stripe_secret_key": "sk_test_new"}),
    ("post", "/api/admin/backup", None),
    ("get", "/api/admin/backup/3/download", None),
    ("post", "/api/admin/backup/restore", {"confirm": "RESTORE", "upload_id": "abc"}),
]


def build_app():
    app = Flask(__name__)
    app.secret_key = "test"
    for module in (auth, admin_api, admin_users, admin_billing, admin_backup):
        app.register_blueprint(module.bp)
    return app


class _Base(unittest.TestCase):
    def setUp(self):
        self.app = build_app()
        FAKE.settings.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _client(self, uid=1, role="admin", stepup_until=None):
        c = self.app.test_client()
        if uid is not None:
            with c.session_transaction() as s:
                s["user_id"] = uid
                s["role"] = role
                if stepup_until is not None:
                    s["stepup_until"] = stepup_until
        return c

    def _send(self, client, method, path, body=None, headers=None):
        with mock.patch.object(FAKE, "query", side_effect=self.q), \
                mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(mailer, "send_async") as send:
            self.sent = send
            return getattr(client, method)(path, data=json.dumps(body) if body is not None else None,
                                           content_type="application/json", headers=headers or {})


class StepUpTest(_Base):
    def test_sensitive_routes_refuse_without_a_recent_password(self):
        for stepup_until in (None, time.time() - 1):
            for method, path, body in STEP_UP_ROUTES:
                with self.subTest(route=f"{method} {path}", stepup_until=stepup_until):
                    self.x.calls.clear()
                    res = self._send(self._client(stepup_until=stepup_until), method, path, body)
                    self.assertEqual((res.status_code, res.get_json()),
                                     (403, {"error": "Confirm your password to continue.",
                                            "code": "step_up_required"}))
                    self.assertEqual(self.x.calls, [], "a refused request must not write")

    def test_the_right_password_opens_a_ten_minute_window(self):
        self.q.routes += [("FROM login_events", {"n": 0}), ("FROM users WHERE id", {"id": 1, "role": "admin", "password_hash": HASH})]
        c = self._client()
        before = time.time()
        res = self._send(c, "post", "/api/admin/step-up", {"password": PASSWORD})
        self.assertEqual(res.status_code, 200)
        with c.session_transaction() as s:
            self.assertAlmostEqual(s["stepup_until"], before + auth.STEP_UP_TTL_SECONDS, delta=5)
        self.assertEqual(self._send(c, "get", "/api/admin/step-up/status").get_json()["active"], True)
        (sql, params), = self.x.sql("INSERT INTO login_events")
        self.assertEqual(params[:4], (1, "step_up", True, None))

    def test_a_wrong_password_does_not_open_the_window(self):
        self.q.routes += [("FROM login_events", {"n": 1}), ("FROM users WHERE id", {"id": 1, "role": "admin", "password_hash": HASH})]
        c = self._client()
        res = self._send(c, "post", "/api/admin/step-up", {"password": "nope"})
        self.assertEqual((res.status_code, res.get_json()), (400, {"error": "That password is not right."}))
        with c.session_transaction() as s:
            self.assertNotIn("stepup_until", s)
        self.assertEqual(self.x.sql("INSERT INTO login_events")[0][1][:4], (1, "step_up", False, "bad_password"))

    def test_the_fifth_wrong_password_signs_the_admin_out(self):
        self.q.routes += [("FROM login_events", {"n": auth.STEP_UP_MAX_FAILURES - 1}),
                          ("FROM users WHERE id", {"id": 1, "role": "admin", "password_hash": HASH})]
        c = self._client()
        res = self._send(c, "post", "/api/admin/step-up", {"password": "nope"})
        self.assertEqual(res.status_code, 401)
        with c.session_transaction() as s:
            self.assertNotIn("user_id", s)
        self.assertEqual(len(self.x.sql("'auth.step_up.locked_out'") or self.x.sql("INSERT INTO audit_log")), 1)

    def test_even_the_right_password_is_refused_once_locked_out(self):
        self.q.routes += [("FROM login_events", {"n": auth.STEP_UP_MAX_FAILURES}),
                          ("FROM users WHERE id", {"id": 1, "role": "admin", "password_hash": HASH})]
        c = self._client()
        self.assertEqual(self._send(c, "post", "/api/admin/step-up", {"password": PASSWORD}).status_code, 401)

    def test_step_up_is_admin_only(self):
        self.assertEqual(self._send(self._client(uid=2, role="user"), "post", "/api/admin/step-up",
                                    {"password": PASSWORD}).status_code, 403)

    def test_saving_billing_config_without_a_new_secret_needs_no_step_up(self):
        res = self._send(self._client(), "put", "/api/admin/billing/config",
                         {"stripe_secret_key": "••••••••", "billing_trial_days": 14})
        self.assertEqual(res.status_code, 200)


class LoginHistoryTest(_Base):
    USER = {"id": 3, "username": "amy", "role": "user", "status": "active", "password_hash": HASH,
            "preferences": {}, "email": "amy@example.com"}

    def _login(self, body, ua=IPHONE, ip="203.0.113.9"):
        return self._send(self.app.test_client(), "post", "/api/auth/login", body,
                          headers={"User-Agent": ua, "X-Real-IP": ip})

    def _event(self):
        (sql, params), = self.x.sql("INSERT INTO login_events")
        cols = ["user_id", "kind", "ok", "reason", "identity", "ip", "ip_prefix", "user_agent",
                "browser", "os", "device", "country", "new_network"]
        return dict(zip(cols, params, strict=True))

    def test_every_outcome_is_recorded(self):
        cases = [
            # (user row or None, password, expected subset of the event)
            (None, PASSWORD, {"user_id": None, "ok": False, "reason": "unknown_user", "identity": "ghost"}),
            (self.USER, "wrong", {"user_id": 3, "ok": False, "reason": "bad_password", "identity": None}),
            ({**self.USER, "status": "locked"}, PASSWORD, {"user_id": 3, "ok": False, "reason": "blocked"}),
            (self.USER, PASSWORD, {"user_id": 3, "ok": True, "reason": None, "new_network": False}),
        ]
        for row, password, expected in cases:
            with self.subTest(expected=expected):
                self.q = _stubs.Router([("FROM users WHERE username", row)])
                self.x = _stubs.Router(default=1)
                self._login({"username": "Ghost" if row is None else "amy", "password": password})
                event = self._event()
                self.assertEqual({k: event[k] for k in expected}, expected)
                self.assertEqual((event["kind"], event["ip"], event["ip_prefix"], event["browser"], event["device"]),
                                 ("password", "203.0.113.9", "203.0.113.0/24", "Safari 18", "mobile"))

    def test_an_admin_on_a_new_network_is_flagged_and_emailed(self):
        admin = {**self.USER, "role": "admin"}
        cases = [
            # (any earlier sign-in, one from this network) -> flagged
            ({"any_before": True, "here_before": False}, True),
            ({"any_before": True, "here_before": True}, False),
            ({"any_before": False, "here_before": False}, False),
        ]
        for seen, flagged in cases:
            with self.subTest(seen=seen):
                self.q = _stubs.Router([("FROM users WHERE username", admin), ("AS any_before", seen)])
                self.x = _stubs.Router(default=1)
                self._login({"username": "amy", "password": PASSWORD})
                self.assertEqual(self._event()["new_network"], flagged)
                self.assertEqual(self.sent.call_count, 1 if flagged else 0)
                if flagged:
                    self.assertEqual(self.sent.call_args.args[:2], ("admin_new_login", "amy@example.com"))

    def test_regular_users_are_never_checked_for_new_networks(self):
        self.q.routes.append(("FROM users WHERE username", self.USER))
        self._login({"username": "amy", "password": PASSWORD})
        self.assertEqual(self.q.sql("AS any_before"), [])


class AuditTest(_Base):
    def test_admin_requests_record_actor_target_ip_and_agent(self):
        app = build_app()
        with app.test_request_context("/api/admin/users/5/lock", headers={"User-Agent": "UA", "X-Real-IP": "198.51.100.4"}), \
                mock.patch.object(FAKE, "execute", side_effect=self.x):
            from flask import session
            session["user_id"] = 1
            util.audit("user.lock", {"id": 5}, target=5)
        (sql, params), = self.x.sql("INSERT INTO audit_log")
        self.assertEqual(params, (1, "user.lock", '{"id": 5}', 5, "198.51.100.4", "UA", True))

    def test_outside_a_request_there_is_no_session_to_read(self):
        """Background jobs and timers used to raise here, and the caller swallowed it: every email
        sent from a job went unrecorded."""
        app = build_app()
        with app.app_context(), mock.patch.object(FAKE, "execute", side_effect=self.x):
            util.audit("email.send", {"template": "welcome"}, user_id=7)
            util.audit("admin.restore.complete", {"job_id": 2}, user_id=1)
        self.assertEqual([p for _, p in self.x.sql("INSERT INTO audit_log")],
                         [(7, "email.send", '{"template": "welcome"}', None, None, None, False),
                          (1, "admin.restore.complete", '{"job_id": 2}', None, None, None, True)])

    def test_admin_actions_are_pruned_on_their_own_longer_clock(self):
        FAKE.settings.update({"audit_retention_days": "90", "admin_audit_retention_days": "1000"})
        with build_app().app_context(), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util.random, "random", return_value=0.0):
            util.audit("auth.login", user_id=1)
        (sql, params), = self.x.sql("DELETE FROM audit_log")
        self.assertIn("NOT by_admin AND created_at", sql)
        self.assertEqual(params, (90, 1000))


class AdminFixesTest(_Base):
    def test_status_and_email_sorts_reach_the_sql(self):
        for key, column in (("status", "u.status DESC"), ("email", "lower(u.email) DESC")):
            with self.subTest(key=key):
                self.q = _stubs.Router()
                self._send(self._client(), "get", f"/api/admin/users?sort={key}&dir=desc")
                self.assertIn(f"ORDER BY {column}", self.q.sql("FROM users u")[0][0])

    def test_setting_a_password_signs_the_user_out_everywhere(self):
        self.q.routes.append(("FROM users WHERE id", {"id": 5, "username": "eve", "role": "user", "status": "active"}))
        res = self._send(self._client(stepup_until=time.time() + 60), "put", "/api/admin/users/5/password",
                         {"password": "a-long-enough-password"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("session_epoch = session_epoch + 1", self.x.sql("UPDATE users")[0][0])

    def test_created_users_get_an_explicit_subscription_row(self):
        for access, fragment in (("trial", "'trialing', now() + make_interval"), ("comped", "'comped', 'infinity'")):
            with self.subTest(access=access):
                self.q = _stubs.Router()
                self.x = _stubs.Router([("INSERT INTO users", {"id": 12})], default=1)
                with mock.patch("seed_categories.seed_for_user"):
                    res = self._send(self._client(), "post", "/api/admin/users",
                                     {"username": "new", "password": "a-long-enough-password", "access": access})
                self.assertEqual(res.status_code, 201)
                self.assertIn(fragment, self.x.sql("INSERT INTO subscriptions")[0][0])
                self.assertEqual(self.x.sql("INSERT INTO audit_log")[0][1][3], 12, "the new user is the target")

    def test_unknown_access_is_rejected(self):
        res = self._send(self._client(), "post", "/api/admin/users",
                         {"username": "new", "password": "a-long-enough-password", "access": "free"})
        self.assertEqual(res.status_code, 400)

    def test_activity_log_hides_what_people_wrote(self):
        rows = [{"id": 2, "user_id": 5, "username": "eve", "action": "statement.upload", "by_admin": False,
                 "detail": {"id": 9, "filename": "march.pdf", "kind": "pdf"}, "target_user_id": None,
                 "target_username": None, "ip": None, "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc)},
                {"id": 1, "user_id": 1, "username": "admin", "action": "user.lock", "by_admin": True,
                 "detail": {"id": 5, "username": "eve", "reason": "spam"}, "target_user_id": 5,
                 "target_username": "eve", "ip": "198.51.100.4", "created_at": datetime(2026, 9, 1, tzinfo=timezone.utc)}]
        self.q.routes.append(("FROM audit_log a", rows))
        items = self._send(self._client(), "get", "/api/admin/audit").get_json()["items"]
        self.assertEqual([(i["detail"], i.get("hidden_fields")) for i in items],
                         [({"id": 9, "kind": "pdf"}, 1), ({"id": 5, "username": "eve", "reason": "spam"}, None)])

    def test_filtering_by_user_includes_what_admins_did_to_them(self):
        self.q.routes.append(("FROM audit_log a", []))
        self._send(self._client(), "get", "/api/admin/audit?user_id=5&admin=1")
        sql, params = self.q.sql("FROM audit_log a")[0]
        self.assertIn("a.target_user_id = %(user_id)s", sql)
        self.assertEqual((params["user_id"], params["admin_only"]), (5, True))

    def test_csv_export_is_audited(self):
        self.q.routes.append(("FROM audit_log a", []))
        res = self._send(self._client(stepup_until=time.time() + 60), "get", "/api/admin/audit?format=csv")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.x.sql("INSERT INTO audit_log")[0][1][1], "admin.audit.export")

    def test_billing_block_reports_the_real_comp_date(self):
        future = datetime(2027, 1, 31, tzinfo=timezone.utc)
        cases = [(datetime(9999, 12, 31, 23, 59, tzinfo=timezone.utc), "forever"),
                 (future, future.isoformat()), (None, None)]
        for comped, expected in cases:
            with self.subTest(comped=comped):
                row = {"id": 5, "role": "user", "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                       "sub_status": "comped" if comped else "active", "comped_until": comped}
                self.assertEqual(admin_billing._block(row)["comped_until"], expected)


if __name__ == "__main__":
    unittest.main()
