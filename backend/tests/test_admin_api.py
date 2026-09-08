import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import admin_api  # noqa: E402
import seed_categories  # noqa: E402
import util  # noqa: E402

# One row that satisfies every aggregate in the overview, so a Router default can stand in for
# all of them without listing each query.
STAT_ROW = {"n": 0, "total": 0, "calls": 0, "errors": 0, "source_bytes": 0, "unique_files": 0,
            "audit_rows": 0, "oldest_audit_at": None, "transactions": 0}


def stat_routes(extra=None):
    """The overview runs a mix of single-row aggregates and list queries; the list ones must come
    back as lists or rows_json has nothing to iterate."""
    lists = ["generate_series", "action = 'auth.login'", "FROM transactions WHERE created_at",
             "FROM statements WHERE created_at", "GROUP BY model", "COALESCE(bank_profile",
             "SELECT file_kind AS kind", "GROUP BY 1,2", "FROM audit_log WHERE user_id"]
    users_rollup = {"total": 4, "active": 3, "locked": 1, "deleted": 0, "admins": 1,
                    "active_7d": 2, "active_30d": 3, "never_signed_in": 1, "new_30d": 1}
    return _stubs.Router((extra or []) + [("FILTER (WHERE status='locked')", users_rollup)]
                         + [(needle, []) for needle in lists], default=STAT_ROW)

# Every route the blueprint exposes; the admin-only test walks all of them.
ROUTES = [
    ("get", "/api/admin/users"), ("post", "/api/admin/users"),
    ("get", "/api/admin/users/5"), ("put", "/api/admin/users/5"),
    ("put", "/api/admin/users/5/password"),
    ("post", "/api/admin/users/5/lock"), ("post", "/api/admin/users/5/unlock"),
    ("post", "/api/admin/users/5/restore"), ("delete", "/api/admin/users/5"),
    ("get", "/api/admin/stats/overview"), ("get", "/api/admin/audit"),
    ("get", "/api/admin/audit/actions"), ("get", "/api/admin/settings"),
    ("put", "/api/admin/settings"),
]


class AdminApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(admin_api.bp)
        FAKE.settings.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None, uid=1, role="admin"):
        c = self.app.test_client()
        if uid is not None:
            with c.session_transaction() as s:
                s["user_id"] = uid
                s["role"] = role
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

    def test_admin_cannot_lock_demote_or_delete_self(self):
        self.q.routes.append(("FROM users WHERE id", {"id": 1, "username": "me", "role": "admin", "status": "active"}))
        cases = [
            (("post", "/api/admin/users/1/lock", None), "You cannot lock your own account"),
            (("delete", "/api/admin/users/1", None), "You cannot delete your own account"),
            (("put", "/api/admin/users/1", {"role": "user"}), "You cannot remove your own admin role"),
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
        for method, path, body in (("put", "/api/admin/users/5", {"role": "user"}),
                                   ("post", "/api/admin/users/5/lock", None),
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
        self.assertEqual((res.status_code, res.get_json()), (201, {"id": 7}))
        self.assertEqual(seed.call_args.args, (FAKE, 7))
        self.assertEqual(self.x.sql("INSERT INTO users")[0][1][0], "eve")

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
                                   ("post", "/api/admin/users/5/lock", None),
                                   ("get", "/api/admin/users/5", None)):
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

    # ---------- listing ----------

    def test_sort_key_cannot_be_injected(self):
        """ruff's S608 is globally off, so the whitelist is the only thing standing between a
        query parameter and the ORDER BY clause."""
        for raw, expected in (("sort=;DROP%20TABLE%20users", "ORDER BY u.id ASC"),
                              ("sort=username&dir=desc", "ORDER BY u.username DESC"),
                              ("sort=' OR 1=1--&dir=;DELETE", "ORDER BY u.id ASC")):
            with self.subTest(raw=raw):
                self.q.calls.clear()
                self._call("get", f"/api/admin/users?{raw}")
                sql = self.q.sql("FROM users u")[0][0]
                self.assertNotIn("DROP TABLE", sql)
                self.assertNotIn("DELETE", sql)
                self.assertIn(expected, sql)

    def test_listing_hides_deleted_users_by_default(self):
        self._call("get", "/api/admin/users")
        self.assertEqual(self.q.sql("FROM users u")[0][1]["statuses"], ["active", "locked"])
        self.q.calls.clear()
        self._call("get", "/api/admin/users?status=deleted")
        self.assertEqual(self.q.sql("FROM users u")[0][1]["statuses"], ["deleted"])

    def test_storage_is_deduped_by_content_hash(self):
        """The same file uploaded twice is two statements rows but one file on disk."""
        self._call("get", "/api/admin/users")
        self.assertIn("DISTINCT ON (user_id, file_sha256)", self.q.sql("FROM users u")[0][0])


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
        q = query or stat_routes()
        with mock.patch.object(FAKE, "query", side_effect=q), \
                mock.patch.object(FAKE, "execute", side_effect=execute or _stubs.Router(default=1)), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(admin_api, "db", FAKE):
            return c.get(path)

    def test_overview_survives_an_unreadable_statements_volume(self):
        """A dashboard must never 500 because a volume is unmounted."""
        with mock.patch.object(admin_api.os, "scandir", side_effect=OSError("not mounted")):
            res = self._get("/api/admin/stats/overview")
        self.assertEqual(res.status_code, 200)
        storage = res.get_json()["storage"]
        self.assertEqual((storage["disk_scan_ok"], storage["disk_bytes"], storage["derived_bytes"]),
                         (False, None, None))

    def test_overview_reports_both_definitions_of_active(self):
        with mock.patch.object(admin_api, "_disk_usage", return_value=(0, True)):
            body = self._get("/api/admin/stats/overview").get_json()
        for key in ("active_7d", "active_30d", "active_by_activity_30d", "never_signed_in"):
            self.assertIn(key, body["users"])
        for key in ("users", "totals", "series", "storage", "ai", "imports", "housekeeping"):
            self.assertIn(key, body)

    def test_overview_is_served_from_cache_until_it_expires(self):
        q = stat_routes()
        with mock.patch.object(admin_api, "_disk_usage", return_value=(0, True)):
            self._get("/api/admin/stats/overview", query=q)
            first = len(q.calls)
            self._get("/api/admin/stats/overview", query=q)
            self.assertEqual(len(q.calls), first, "a warm cache must not re-run the aggregates")
            self._get("/api/admin/stats/overview?refresh=1", query=q)
            self.assertGreater(len(q.calls), first, "refresh=1 must bypass the cache")

    def test_user_detail_counts_the_prefixed_settings_rows(self):
        q = stat_routes([("FROM users WHERE id", {"id": 5, "username": "eve", "role": "user", "status": "active",
                                                   "preferences": {"theme": "dark"}})])
        with mock.patch.object(admin_api, "_disk_usage", return_value=(0, True)):
            res = self._get("/api/admin/users/5", query=q)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(q.sql("settings WHERE key LIKE")[0][1]["pfx"], "u5:%")
        self.assertEqual(res.get_json()["user"]["preferences_keys"], ["theme"])

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
