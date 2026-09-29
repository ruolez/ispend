"""Admin › Users: list filters and paging, export, bulk actions, the one-person view and its
actions, notes, tags and saved views."""
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

from flask import Flask  # noqa: E402

import admin_api  # noqa: E402
import admin_users  # noqa: E402
import auth  # noqa: E402
import mailer  # noqa: E402

CREATED = datetime(2026, 9, 1, tzinfo=timezone.utc)
PERSON = {"id": 5, "username": "eve", "email": "eve@example.com", "email_verified_at": None,
          "verification_required": False, "role": "user", "status": "active", "locked_at": None, "lock_reason": None,
          "deleted_at": None, "created_at": CREATED, "last_login_at": None, "last_seen_at": None,
          "last_active_at": None, "first_upload_at": None, "first_commit_at": None, "preferences": {}}


class FiltersTest(unittest.TestCase):
    def test_each_filter_becomes_a_parameterised_clause(self):
        cases = [
            ({}, [], {"statuses": ["active", "locked"]}),
            ({"status": "all"}, [], {"statuses": ["active", "locked", "deleted"]}),
            ({"status": "nonsense", "role": "root"}, [], {"statuses": ["active", "locked"]}),
            ({"q": "#12"}, ["(u.id = %(qid)s OR u.username ILIKE %(q)s OR u.email ILIKE %(q)s)"], {"qid": 12, "q": "%#12%"}),
            ({"q": "eve"}, ["(u.username ILIKE %(q)s OR u.email ILIKE %(q)s)"], {"q": "%eve%"}),
            ({"state": "trialing,comped,bogus"}, ["(s.ent_state = ANY(%(states)s) OR (s.comped_until IS NOT NULL AND s.comped_until > now()))"],
             {"states": ["trialing"]}),
            ({"plan": "yearly"}, ["s.plan = %(plan)s AND s.mrr_cents > 0"], {"plan": "yearly"}),
            ({"trial_ending": "7"}, ["s.ent_state = 'trialing' AND s.trial_end < now() + make_interval(days => %(trial_days)s)"],
             {"trial_days": 7}),
            ({"trial_ending": "9999"}, [], {}),
            ({"confirmed": "0"}, ["u.email_verified_at IS NULL"], {}),
            ({"activated": "1"}, [admin_users.ACTIVATED], {}),
            ({"activated": "0"}, [f"NOT {admin_users.ACTIVATED}"], {}),
            ({"inactive_for": "30"}, ["(u.last_seen_at IS NULL OR u.last_seen_at < now() - make_interval(days => %(inactive_for)s))"],
             {"inactive_for": 30}),
            ({"source": "Search"}, ["sa.channel = %(source)s"], {"source": "search"}),
            ({"source": "unknown"}, ["sa.user_id IS NULL"], {}),
            ({"tag": "3,x,4"}, ["EXISTS (SELECT 1 FROM user_admin_tags ut WHERE ut.user_id = u.id AND ut.tag_id = ANY(%(tags)s))"],
             {"tags": [3, 4]}),
        ]
        for args, extra_where, extra_params in cases:
            with self.subTest(args=args):
                where, params = admin_users.user_filters(args)
                self.assertEqual(where[:2], ["u.role = 'user'", "u.status = ANY(%(statuses)s)"],
                                 "admins are never listed, and trash stays hidden unless asked for")
                self.assertEqual(where[2:], extra_where)
                self.assertEqual({k: v for k, v in params.items() if k != "statuses"},
                                 {k: v for k, v in extra_params.items() if k != "statuses"})
                if "statuses" in extra_params:
                    self.assertEqual(params["statuses"], extra_params["statuses"])

    def test_signup_dates_are_whole_local_days(self):
        _where, params = admin_users.user_filters({"signup_from": "2026-03-01", "signup_to": "2026-03-01"})
        self.assertEqual((params["signup_to"] - params["signup_from"]).days, 1)
        with self.assertRaises(ValueError):
            admin_users.user_filters({"signup_from": "March"})

    def test_sort_key_cannot_be_injected(self):
        """The whitelist is the only thing between a query parameter and ORDER BY."""
        cases = [({"sort": ";DROP TABLE users"}, "u.id ASC NULLS FIRST"),
                 ({"sort": "username", "dir": "desc"}, "lower(u.username) DESC NULLS LAST"),
                 ({"sort": "' OR 1=1--", "dir": ";DELETE"}, "u.id ASC NULLS FIRST"),
                 ({"sort": "mrr", "dir": "desc"}, "s.mrr_cents DESC NULLS LAST")]
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(admin_users._order(args), expected)

    def test_saved_views_hold_list_filters_only(self):
        good = [{"id": "trials", "name": "Trials ending", "query": "?state=trialing&trial_ending=7"}]
        self.assertEqual(admin_users.clean_admin_views(good), good)
        for bad in ([{"id": "x", "name": "n", "query": "?password=1"}], [{"id": "x", "name": "", "query": "?q=a"}],
                    [{"id": "x", "name": "n", "query": "q=a"}], "nope"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                admin_users.clean_admin_views(bad)
        self.assertEqual(auth.clean_preferences({"admin_views": good}), {"admin_views": good})


class _Api(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        for bp in (admin_api.bp, admin_users.bp):
            self.app.register_blueprint(bp)
        FAKE.settings.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None, stepped_up=True, uid=1):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"], s["role"], s["username"] = uid, "admin", "root"
            if stepped_up:
                s["stepup_until"] = time.time() + 600
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(mailer, "send_async") as send:
            self.sent = send
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None,
                                      content_type="application/json")


class ListTest(_Api):
    def test_pages_are_fetched_first_and_detailed_after(self):
        self.q.routes += [("COUNT(*) OVER ()", [{"id": 9, "total": 61}, {"id": 4, "total": 61}]),
                          ("WHERE u.id = ANY", [{"id": 4, "username": "a", "email_verified_at": None, "tags": []},
                                                {"id": 9, "username": "b", "email_verified_at": CREATED, "tags": []}])]
        res = self._call("get", "/api/admin/users?page=2&per_page=2&sort=mrr&dir=desc")
        body = res.get_json()
        self.assertEqual(([i["id"] for i in body["items"]], body["total"], body["page"]), ([9, 4], 61, 2),
                         "rows come back in the sorted order, not the detail query's")
        self.assertEqual([i["email_confirmed"] for i in body["items"]], [True, False])
        sql, params = self.q.sql("COUNT(*) OVER ()")[0]
        self.assertEqual((params["limit"], params["offset"]), (2, 2))
        self.assertIn("ORDER BY s.mrr_cents DESC NULLS LAST, u.id", sql)
        self.assertIn("DISTINCT ON (file_sha256)", self.q.sql("WHERE u.id = ANY")[0][0], "storage is deduped by content")

    def test_a_bad_date_is_a_400(self):
        self.assertEqual(self._call("get", "/api/admin/users?signup_from=soon").status_code, 400)

    def test_export_needs_step_up_and_is_audited(self):
        self.assertEqual(self._call("get", "/api/admin/users?format=csv", stepped_up=False).status_code, 403)
        self.q.routes += [("COUNT(*) OVER ()", [{"id": 4, "total": 1}]),
                          ("WHERE u.id = ANY", [{"id": 4, "username": "a", "email_verified_at": None,
                                                 "tags": [{"id": 1, "name": "VIP", "color": "c1"}]}])]
        res = self._call("get", "/api/admin/users?format=csv&state=trialing")
        self.assertEqual(res.status_code, 200)
        text = res.get_data(as_text=True)
        self.assertIn("email_confirmed", text.splitlines()[0])
        self.assertIn("VIP", text)
        self.assertEqual(self.x.sql("INSERT INTO audit_log")[0][1][1], "admin.users.export")


class BulkTest(_Api):
    def test_needs_step_up(self):
        self.assertEqual(self._call("post", "/api/admin/users/bulk", {"action": "lock", "ids": [5]},
                                    stepped_up=False).status_code, 403)

    def test_validation(self):
        cases = [({"action": "delete", "ids": [5]}, 400), ({"action": "lock"}, 400),
                 ({"action": "lock", "ids": list(range(2, 600))}, 413),
                 ({"action": "tag", "ids": [5], "params": {}}, 400),
                 ({"action": "extend_trial", "ids": [5], "params": {"days": 0}}, 400)]
        for body, status in cases:
            with self.subTest(body=body):
                self.assertEqual(self._call("post", "/api/admin/users/bulk", body).status_code, status)

    def test_lock_skips_yourself_and_the_last_admin(self):
        self.q.routes.append(("SELECT id, username, role, status FROM users WHERE id",
                              lambda s, p: {"id": p[0], "username": f"u{p[0]}", "role": "admin" if p[0] == 7 else "user",
                                            "status": "active"}))
        self.x.routes.insert(0, ("UPDATE users SET status = 'locked'", lambda s, p: 0 if p["id"] == 7 else 1))
        res = self._call("post", "/api/admin/users/bulk", {"action": "lock", "ids": [1, 5, 7], "params": {"reason": "spam"}})
        self.assertEqual(res.get_json(), {"affected": 1, "skipped": [{"id": 1, "reason": "yourself"},
                                                                     {"id": 7, "reason": "last administrator"}]})
        self.assertEqual(len([p for s, p in self.x.sql("INSERT INTO audit_log") if p[1] == "user.lock"]), 1)

    def test_by_filter_uses_the_same_filters_as_the_list(self):
        self.q.routes += [("COUNT(*) OVER ()", [{"id": 5, "total": 1}]),
                          ("SELECT id, username, role, status FROM users WHERE id",
                           {"id": 5, "username": "eve", "role": "user", "status": "active"})]
        res = self._call("post", "/api/admin/users/bulk", {"action": "extend_trial", "query": "?state=trialing&password=x",
                                                           "params": {"days": 7}})
        self.assertEqual(res.get_json()["affected"], 1)
        self.assertEqual(self.q.sql("COUNT(*) OVER ()")[0][1]["states"], ["trialing"])
        self.assertIn("trial_end = GREATEST", self.x.sql("UPDATE subscriptions")[0][0])


class PersonTest(_Api):
    def _person_routes(self, **over):
        self.q.routes += [("FROM users WHERE id = %s", {**PERSON, **over}), ("AS accounts", {"accounts": 1}),
                          ("AS calls", {"calls": 0})]

    def test_the_person_view_never_carries_what_they_entered(self):
        self._person_routes()
        with mock.patch.object(admin_api, "_disk_usage", return_value=(0, True)):
            body = self._call("get", "/api/admin/users/5").get_json()
        self.assertEqual(set(body), {"user", "counts", "data_range", "storage", "categorization", "ai",
                                     "recent_activity", "billing", "subscription", "activation", "attribution",
                                     "timeline", "logins", "notes", "tags", "admin_actions", "emails", "reports"})
        self.assertEqual(set(body["counts"]), {"accounts"})
        text = json.dumps(body)
        for word in ("description", "merchant_key", "amount\"", "balance", "filename", "password"):
            self.assertNotIn(word, text)
        self.assertEqual(body["timeline"][0]["kind"], "signup")

    def test_settings_rows_are_counted_through_the_user_prefix(self):
        self._person_routes()
        with mock.patch.object(admin_api, "_disk_usage", return_value=(0, True)):
            self._call("get", "/api/admin/users/5")
        self.assertEqual(self.q.sql("settings WHERE key LIKE")[0][1]["pfx"], "u5:%")

    def test_account_actions(self):
        cases = [
            ("post", "/api/admin/users/5/mark-confirmed", None, {}, 200, "email_verified_at = COALESCE"),
            ("post", "/api/admin/users/5/sign-out-all", None, {}, 200, "session_epoch = session_epoch + 1"),
            ("post", "/api/admin/users/1/sign-out-all", None, {}, 400, None),
            ("post", "/api/admin/users/5/resend-confirmation", None, {"email_verified_at": CREATED}, 409, None),
            ("post", "/api/admin/users/5/send-password-reset", None, {"status": "locked"}, 409, None),
            ("put", "/api/admin/users/5/email", {"email": "not-an-email"}, {}, 400, None),
        ]
        for method, path, body, over, status, fragment in cases:
            with self.subTest(path=path, over=over):
                self.q = _stubs.Router([("FROM users WHERE id = %s", {**PERSON, **over})])
                self.x = _stubs.Router(default=1)
                res = self._call(method, path, body)
                self.assertEqual(res.status_code, status, res.get_json())
                if fragment:
                    self.assertTrue(any(fragment in s for s, _ in self.x.calls), fragment)

    def test_password_reset_is_emailed_not_typed(self):
        self.q.routes += [("FROM users WHERE id = %s", PERSON), ("kind = 'reset'", {"n": 0})]
        res = self._call("post", "/api/admin/users/5/send-password-reset")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.sent.call_args.args[:2], ("password_reset", "eve@example.com"))
        self.assertEqual(self.x.sql("INSERT INTO auth_tokens")[0][1][1], "reset")

    def test_changing_an_email_needs_step_up_and_resets_confirmation(self):
        self.assertEqual(self._call("put", "/api/admin/users/5/email", {"email": "new@example.com"},
                                    stepped_up=False).status_code, 403)
        self.q.routes += [("FROM users WHERE id = %s", PERSON)]
        res = self._call("put", "/api/admin/users/5/email", {"email": "New@Example.com"})
        self.assertEqual(res.get_json(), {"ok": True, "email": "new@example.com"})
        self.assertIn("email_verified_at = NULL", self.x.sql("UPDATE users SET email")[0][0])
        self.assertEqual(self.sent.call_args.args[0], "verify_email")


class NotesAndTagsTest(_Api):
    def test_notes(self):
        self.q.routes += [("FROM users WHERE id = %s", PERSON)]
        self.x.routes.insert(0, ("INSERT INTO admin_notes", {"id": 3, "body": "Refunded", "pinned": True,
                                                             "created_at": CREATED, "updated_at": CREATED}))
        res = self._call("post", "/api/admin/users/5/notes", {"body": "  Refunded ", "pinned": True})
        self.assertEqual((res.status_code, res.get_json()["author"]), (201, "root"))
        self.assertEqual(self.x.sql("INSERT INTO admin_notes")[0][1], (5, 1, "Refunded", True))
        self.assertEqual(self._call("post", "/api/admin/users/5/notes", {"body": ""}).status_code, 400)
        self.assertEqual(self._call("delete", "/api/admin/notes/99").status_code, 404)

    def test_tags(self):
        self.q.routes.append(("FROM admin_tags GROUP BY color", [{"color": "c1", "n": 2}]))
        self.x.routes.insert(0, ("INSERT INTO admin_tags", lambda s, p: {"id": 1, "name": p[0], "color": p[1]}))
        res = self._call("post", "/api/admin/tags", {"name": "VIP"})
        self.assertEqual(res.get_json(), {"id": 1, "name": "VIP", "color": "c2"}, "the least-used colour")
        self.q.routes.insert(0, ("lower(name) = lower(%s)", {"?column?": 1}))
        self.assertEqual(self._call("post", "/api/admin/tags", {"name": "vip"}).status_code, 409)
        self.assertEqual(self._call("post", "/api/admin/tags", {"name": "x", "color": "#fff"}).status_code, 400)

    def test_setting_a_person_s_tags_replaces_them(self):
        self.q.routes += [("FROM users WHERE id = %s", PERSON)]
        self._call("put", "/api/admin/users/5/tags", {"tag_ids": [2, "3", "x"]})
        self.assertEqual(self.x.sql("DELETE FROM user_admin_tags")[0][1], (5, [2, 3]))
        self.assertEqual(len(self.x.sql("INSERT INTO user_admin_tags")), 2)


class CreateAndInviteTest(_Api):
    def test_invite_sets_a_random_password_and_emails_a_link(self):
        self.x.routes.insert(0, ("INSERT INTO users", {"id": 12}))
        with mock.patch("seed_categories.seed_for_user"):
            res = self._call("post", "/api/admin/users", {"email": "New@Example.com", "send_invite": True})
        self.assertEqual(res.get_json(), {"id": 12, "invited": True})
        username, email = self.x.sql("INSERT INTO users")[0][1][:2]
        self.assertEqual((username, email), ("new@example.com", "new@example.com"))
        self.assertEqual(self.sent.call_args.args[:2], ("invite", "new@example.com"))
        self.assertIn("invite=1", self.sent.call_args.kwargs["link"])
        self.assertEqual(self.x.sql("INSERT INTO auth_tokens")[0][1][1], "invite")

    def test_an_invite_needs_an_email(self):
        res = self._call("post", "/api/admin/users", {"username": "x", "send_invite": True})
        self.assertEqual(res.get_json()["error"], "An invitation needs an email address")

    def test_the_reset_page_accepts_an_invitation(self):
        app = Flask(__name__)
        app.secret_key = "t"
        app.register_blueprint(auth.bp)
        x = _stubs.Router([("kind = %s", lambda s, p: {"user_id": 12} if p[1] == "invite" else None),
                           ("UPDATE users SET password_hash", {**PERSON, "id": 12, "session_epoch": 1})], default=1)
        with mock.patch.object(FAKE, "execute", side_effect=x), mock.patch.object(FAKE, "query", side_effect=_stubs.Router()):
            res = app.test_client().post("/api/auth/password/reset",
                                         data=json.dumps({"token": "t", "password": "a-long-enough-password"}),
                                         content_type="application/json")
        self.assertEqual(res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
