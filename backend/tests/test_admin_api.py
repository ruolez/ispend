import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import admin_api  # noqa: E402
import seed_categories  # noqa: E402
import util  # noqa: E402

# Every route the blueprint exposes; the admin-only test walks all of them.
ROUTES = [
    ("post", "/api/admin/users"),
    ("put", "/api/admin/users/5/password"),
    ("post", "/api/admin/users/5/lock"), ("post", "/api/admin/users/5/unlock"),
    ("post", "/api/admin/users/5/restore"), ("delete", "/api/admin/users/5"),
    ("get", "/api/admin/audit"),
    ("get", "/api/admin/audit/actions"), ("get", "/api/admin/settings"),
    ("put", "/api/admin/settings"), ("get", "/api/admin/search?q=a"),
    ("get", "/api/admin/step-up/status"), ("post", "/api/admin/step-up"),
    ("get", "/api/admin/me/leftover-data"), ("post", "/api/admin/me/leftover-data/wipe"),
    ("get", "/api/admin/ai-config"), ("put", "/api/admin/ai-config"),
    ("get", "/api/admin/ai-config/models"), ("post", "/api/admin/ai-config/test"),
]


class AdminApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(admin_api.bp)
        FAKE.settings.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None, uid=1, role="admin", stepped_up=True):
        c = self.app.test_client()
        if uid is not None:
            with c.session_transaction() as s:
                s["user_id"] = uid
                s["role"] = role
                if stepped_up:
                    s["stepup_until"] = time.time() + 600
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(admin_api, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None,
                                      content_type="application/json")

    def _writes(self):
        return [c for c in self.x.calls if c[0].lstrip().startswith(("UPDATE users", "DELETE FROM", "INSERT INTO users"))]

    # ---------- access ----------

    def test_every_route_is_admin_only(self):
        for method, path in ROUTES:
            with self.subTest(route=f"{method} {path}"):
                self.x.calls.clear()
                self.assertEqual(self._call(method, path, uid=2, role="user").status_code, 403)
                self.assertEqual(self._call(method, path, uid=None).status_code, 401)
                self.assertEqual(self._writes(), [], "a rejected request must not write")

    # ---------- guards ----------

    def test_admin_cannot_lock_or_delete_self(self):
        self.q.routes.append(("FROM users WHERE id", {"id": 1, "username": "me", "role": "admin", "status": "active"}))
        cases = [
            (("post", "/api/admin/users/1/lock", None), "You cannot lock your own account"),
            (("delete", "/api/admin/users/1", None), "You cannot delete your own account"),
        ]
        for (method, path, body), message in cases:
            with self.subTest(path=path):
                res = self._call(method, path, body, uid=1)
                self.assertEqual((res.status_code, res.get_json()), (400, {"error": message}))
        self.assertEqual(self._writes(), [])

    def test_last_admin_guard_runs_as_one_statement(self):
        """A read-then-write pair loses to two concurrent demotions, so the guard has to live in
        the UPDATE's WHERE clause and be detected by rowcount."""
        self.q.routes.append(("FROM users WHERE id", {"id": 5, "username": "eve", "role": "admin", "status": "active"}))
        for method, path, body in (("post", "/api/admin/users/5/lock", None),
                                   ("delete", "/api/admin/users/5", None)):
            with self.subTest(path=path):
                self.x = _stubs.Router([("UPDATE users", 0)], default=1)
                res = self._call(method, path, body)
                self.assertEqual((res.status_code, res.get_json()), (409, {"error": admin_api.LAST_ADMIN_ERROR}))
                self.assertIn("EXISTS (", self.x.sql("UPDATE users")[0][0])

    # ---------- lifecycle ----------

    def test_state_transitions(self):
        cases = [
            # (stored row, method, path, body, expected status, SQL fragment the write must contain)
            ({"status": "active"}, "post", "/api/admin/users/5/lock", {"reason": "spam"}, "locked", "status = 'locked'"),
            ({"status": "locked"}, "post", "/api/admin/users/5/unlock", None, "active", "status = 'active'"),
            ({"status": "active"}, "delete", "/api/admin/users/5", None, "deleted", "status = 'deleted'"),
        ]
        for row, method, path, body, expected, fragment in cases:
            with self.subTest(path=path):
                self.q = _stubs.Router([("FROM users WHERE id", {"id": 5, "username": "eve", "role": "user", **row})])
                self.x = _stubs.Router(default=1)
                res = self._call(method, path, body)
                self.assertEqual((res.status_code, res.get_json()["status"]), (200, expected))
                self.assertIn(fragment, self.x.sql("UPDATE users")[0][0])

    def test_restore_returns_a_previously_locked_user_to_locked(self):
        """Someone locked for cause and then deleted must not come back able to sign in."""
        for locked_at, expected in ((None, "active"), ("2026-01-01T00:00:00+00:00", "locked")):
            with self.subTest(locked_at=locked_at):
                self.q = _stubs.Router([("FROM users WHERE id", {"id": 5, "username": "eve", "role": "user",
                                                                 "status": "deleted", "locked_at": locked_at})])
                self.x = _stubs.Router(default=1)
                res = self._call("post", "/api/admin/users/5/restore")
                self.assertEqual(res.get_json(), {"ok": True, "status": expected})

    def test_restore_can_be_forced_to_active(self):
        self.q.routes.append(("FROM users WHERE id", {"id": 5, "username": "eve", "role": "user",
                                                      "status": "deleted", "locked_at": "2026-01-01T00:00:00+00:00"}))
        res = self._call("post", "/api/admin/users/5/restore", {"to": "active"})
        self.assertEqual(res.get_json(), {"ok": True, "status": "active"})

    # ---------- purge ----------

    def _deleted_user(self):
        return _stubs.Router([("FROM users WHERE id", {"id": 5, "username": "eve", "role": "user",
                                                       "status": "deleted", "locked_at": None}),
                              ("FROM statements", None), ("COUNT(*) AS n FROM transactions", {"n": 12})])

    def test_purge_requires_the_user_to_be_in_the_trash_first(self):
        self.q.routes.append(("FROM users WHERE id", {"id": 5, "username": "eve", "role": "user", "status": "active"}))
        res = self._call("delete", "/api/admin/users/5?permanent=true&confirm=eve")
        self.assertEqual(res.status_code, 409)
        self.assertEqual([c for c in self.x.calls if c[0].startswith("DELETE FROM users")], [])

    def test_purge_requires_a_matching_confirm_phrase(self):
        self.q = self._deleted_user()
        res = self._call("delete", "/api/admin/users/5?permanent=true&confirm=nope")
        self.assertEqual((res.status_code, res.get_json()), (409, {"error": "Type the username to confirm"}))
        self.assertEqual([c for c in self.x.calls if c[0].startswith("DELETE FROM users")], [])

    def test_purge_is_blocked_while_an_import_is_running(self):
        self.q = _stubs.Router([("FROM users WHERE id", {"id": 5, "username": "eve", "role": "user",
                                                         "status": "deleted", "locked_at": None}),
                                ("FROM statements", {"?column?": 1})])
        res = self._call("delete", "/api/admin/users/5?permanent=true&confirm=eve")
        self.assertEqual(res.status_code, 409)
        self.assertEqual([c for c in self.x.calls if c[0].startswith("DELETE FROM users")], [])

    def test_purge_removes_rows_prefixed_settings_and_files(self):
        self.q = self._deleted_user()
        with mock.patch.object(admin_api.shutil, "rmtree") as rm:
            res = self._call("delete", "/api/admin/users/5?permanent=true&confirm=eve")
        self.assertEqual((res.status_code, res.get_json()), (200, {"ok": True, "purged": True}))
        self.assertEqual([p for _s, p in self.x.sql("DELETE FROM users")], [(5,)])
        # Per-user settings live under a 'u<id>:' prefix in the global table; nothing else cleans them.
        self.assertEqual([p for _s, p in self.x.sql("DELETE FROM settings WHERE key LIKE")], [("u5:%",)])
        self.assertTrue(rm.call_args.args[0].endswith("/5"))
        # audit_log.user_id goes NULL on delete, so the username has to survive in the detail.
        self.assertEqual(json.loads(self.x.sql("INSERT INTO audit_log")[0][1][2]),
                         {"id": 5, "username": "eve", "txn_count": 12})

    # ---------- create ----------

    def test_create_user_seeds_categories(self):
        self.x.routes.append(("INSERT INTO users", {"id": 7}))
        with mock.patch.object(seed_categories, "seed_for_user") as seed:
            res = self._call("post", "/api/admin/users", {"username": "eve", "password": "secret1234x", "role": "user"})
        self.assertEqual((res.status_code, res.get_json()), (201, {"id": 7, "invited": False}))
        self.assertEqual(seed.call_args.args, (FAKE, 7))
        self.assertEqual(self.x.sql("INSERT INTO users")[0][1][0], "eve")

    def test_the_console_only_creates_customers(self):
        """There is one kind of admin and it is never made from here."""
        self.x.routes.append(("INSERT INTO users", {"id": 7}))
        with mock.patch.object(seed_categories, "seed_for_user"):
            self._call("post", "/api/admin/users", {"username": "eve", "password": "secret1234x", "role": "admin"})
        self.assertIn("VALUES (%s, %s, %s, 'user')", self.x.sql("INSERT INTO users")[0][0])
        self.assertEqual(self._call("put", "/api/admin/users/5", {"role": "admin"}).status_code, 405)

    def test_admin_accounts_are_not_people_in_the_console(self):
        self._call("post", "/api/admin/users/5/lock")
        self._call("get", "/api/admin/search?q=eve")
        for needle in ("FROM users WHERE id", "FROM users u LEFT JOIN subscriptions"):
            with self.subTest(sql=needle):
                self.assertIn("role = 'user'", self.q.sql(needle)[0][0])

    def test_create_user_validation_and_duplicates(self):
        res = self._call("post", "/api/admin/users", {"username": "eve", "password": "abc"})
        self.assertEqual(res.status_code, 400)
        self.q.routes.append(("FROM users WHERE username", {"id": 3, "status": "active", "username": "eve"}))
        res = self._call("post", "/api/admin/users", {"username": "eve", "password": "secret1234x"})
        self.assertEqual(res.get_json()["error"], "Username already exists")

    def test_create_user_offers_restore_for_a_deleted_username(self):
        self.q.routes.append(("FROM users WHERE username", {"id": 12, "status": "deleted", "username": "eve"}))
        res = self._call("post", "/api/admin/users", {"username": "eve", "password": "secret1234x"})
        self.assertEqual(res.status_code, 409)
        self.assertEqual((res.get_json()["code"], res.get_json()["user_id"]), ("username_deleted", 12))
        self.assertEqual(self.x.sql("INSERT INTO users"), [])

    def test_unknown_user_ids_are_404(self):
        for method, path, body in (("delete", "/api/admin/users/5", None),
                                   ("put", "/api/admin/users/5/password", {"password": "a" * 10}),
                                   ("post", "/api/admin/users/5/lock", None)):
            with self.subTest(path=path):
                res = self._call(method, path, body)
                self.assertEqual((res.status_code, res.get_json()), (404, {"error": "User not found"}), path)
        self.assertEqual(self._writes(), [])

    def test_passwords_shorter_than_the_policy_are_rejected(self):
        short = "a" * 9
        res = self._call("post", "/api/admin/users", {"username": "eve", "password": short})
        self.assertEqual(res.get_json(), {"error": "Username and a password of at least 10 characters are required"})
        res = self._call("put", "/api/admin/users/5/password", {"password": short})
        self.assertEqual(res.get_json(), {"error": "Password must be at least 10 characters"})


class AdminStatsTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(admin_api.bp)
        FAKE.settings.clear()

    def _get(self, path, query=None, execute=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
            s["role"] = "admin"
        q = query or _stubs.Router()
        with mock.patch.object(FAKE, "query", side_effect=q), \
                mock.patch.object(FAKE, "execute", side_effect=execute or _stubs.Router(default=1)), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(admin_api, "db", FAKE):
            return c.get(path)

    def test_audit_uses_keyset_pagination_and_survives_purged_users(self):
        rows = [{"id": 900 - i, "user_id": None, "username": None, "action": "user.purge",
                 "detail": {"username": "eve"}, "created_at": None} for i in range(2)]
        q = _stubs.Router([("FROM audit_log a", rows)])
        res = self._get("/api/admin/audit?limit=2&user_id=5&action=user.purge&cursor=901", query=q)
        sql, params = q.sql("FROM audit_log a")[0]
        self.assertIn("ORDER BY a.id DESC", sql)
        # LEFT JOIN, because audit_log.user_id goes NULL when a user is purged.
        self.assertIn("LEFT JOIN users u", sql)
        self.assertEqual((params["cursor"], params["user_id"], params["action"]), (901, 5, "user.purge"))
        self.assertEqual(res.get_json()["next_cursor"], 899)

    def test_audit_retention_setting_is_range_checked(self):
        x = _stubs.Router(default=1)
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
            s["role"] = "admin"
        with mock.patch.object(FAKE, "query", side_effect=_stubs.Router(default=None)), \
                mock.patch.object(FAKE, "execute", side_effect=x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(admin_api, "db", FAKE):
            bad = c.put("/api/admin/settings", data=json.dumps({"audit_retention_days": 3}),
                        content_type="application/json")
            good = c.put("/api/admin/settings", data=json.dumps({"audit_retention_days": 365}),
                         content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(good.status_code, 200)
        self.assertEqual(FAKE.settings["audit_retention_days"], "365")


if __name__ == "__main__":
    unittest.main()
