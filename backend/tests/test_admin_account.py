"""Admin accounts run the console and nothing else: no customer app, no finance data, and the
instance AI key lives in the console instead of the admin's personal settings."""
import json
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()
_stubs.install_stripe()

from flask import Flask  # noqa: E402

import accounts_api  # noqa: E402
import admin_api  # noqa: E402
import admin_metrics  # noqa: E402
import ai_api  # noqa: E402
import auth  # noqa: E402
import billing_api  # noqa: E402
import budgets_api  # noqa: E402
import categories_api  # noqa: E402
import import_layouts_api  # noqa: E402
import merchants_api  # noqa: E402
import openrouter  # noqa: E402
import privacy  # noqa: E402
import public_api  # noqa: E402
import reports_api  # noqa: E402
import review_api  # noqa: E402
import rules_api  # noqa: E402
import settings_api  # noqa: E402
import statements_api  # noqa: E402
import tags_api  # noqa: E402
import transactions_api  # noqa: E402
import util  # noqa: E402
from backup import tables  # noqa: E402

CUSTOMER_APP = (accounts_api, ai_api, billing_api, budgets_api, categories_api, import_layouts_api, merchants_api,
                reports_api, review_api, rules_api, settings_api, statements_api, tags_api, transactions_api)

# One request per customer-app area, plus the customer's own data rights in the auth blueprint.
CLOSED_TO_ADMINS = [
    ("get", "/api/transactions"), ("get", "/api/statements"), ("post", "/api/statements"),
    ("get", "/api/accounts"), ("get", "/api/categories"), ("get", "/api/rules"), ("get", "/api/budgets"),
    ("get", "/api/reports/summary"), ("get", "/api/review"), ("get", "/api/settings"),
    ("put", "/api/settings"), ("get", "/api/insights"), ("post", "/api/billing/checkout"),
    ("post", "/api/billing/portal"), ("get", "/api/billing/status"),
    ("post", "/api/auth/me/exports"), ("get", "/api/auth/me/exports"), ("delete", "/api/auth/me"),
]
OPEN_TO_ADMINS = [
    ("get", "/api/auth/me"), ("put", "/api/auth/me/preferences"), ("put", "/api/auth/me/password"),
    ("post", "/api/auth/logout"), ("get", "/api/admin/me/leftover-data"), ("get", "/api/admin/ai-config"),
    ("get", "/api/public/landing"), ("post", "/api/billing/webhook"), ("get", "/api/health"),
]


def build_app():
    app = Flask(__name__)
    app.secret_key = "test"
    for module in (auth, admin_api, admin_metrics, public_api, *CUSTOMER_APP):
        app.register_blueprint(module.bp)
    app.add_url_rule("/api/health", "health", lambda: {"status": "ok"})
    app.before_request(auth.admin_scope_guard)
    return app


class _Api(unittest.TestCase):
    def setUp(self):
        self.app = build_app()
        FAKE.settings.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None, uid=1, role="admin", stepped_up=False):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"], s["role"] = uid, role
            if stepped_up:
                s["stepup_until"] = time.time() + 600
        with mock.patch.object(FAKE, "query", side_effect=self.q), \
                mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(openrouter, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None,
                                      content_type="application/json")


class GuardTest(_Api):
    def test_every_customer_area_refuses_an_admin_before_touching_data(self):
        for method, path in CLOSED_TO_ADMINS:
            with self.subTest(route=f"{method} {path}"):
                self.q.calls.clear()
                self.x.calls.clear()
                res = self._call(method, path, {})
                self.assertEqual((res.status_code, res.get_json()["code"]), (403, auth.ADMIN_ACCOUNT_CODE))
                self.assertEqual((self.q.calls, self.x.calls), ([], []))

    def test_the_console_sign_in_and_anonymous_endpoints_stay_open(self):
        self.q.routes.append(("FROM users WHERE id", {"id": 1, "username": "admin", "role": "admin",
                                                      "preferences": {}, "password_hash": "x"}))
        for method, path in OPEN_TO_ADMINS:
            with self.subTest(route=f"{method} {path}"):
                res = self._call(method, path, {})
                self.assertNotEqual((res.get_json(silent=True) or {}).get("code"), auth.ADMIN_ACCOUNT_CODE)

    def test_customers_are_not_affected(self):
        for method, path in CLOSED_TO_ADMINS:
            with self.subTest(route=f"{method} {path}"):
                res = self._call(method, path, {}, uid=2, role="user")
                self.assertNotEqual((res.get_json(silent=True) or {}).get("code"), auth.ADMIN_ACCOUNT_CODE)

    def test_a_new_customer_blueprint_is_closed_by_default(self):
        """The guard is an allowlist: a blueprint nobody listed is the customer app."""
        for module in CUSTOMER_APP:
            with self.subTest(blueprint=module.bp.name):
                self.assertFalse(auth.admin_may_use(module.bp.name, f"{module.bp.name}.anything"))
        self.assertFalse(auth.admin_may_use("budgets_v2", "budgets_v2.list"))
        self.assertTrue(auth.admin_may_use("admin_newthing", "admin_newthing.list"))


class LeftoverDataTest(_Api):
    COUNTS = {"transactions": 1238, "statements": 12, "accounts": 8, "rules": 209, "categories": 60, "budgets": 0}

    def test_the_counts_say_whether_anything_is_left(self):
        for counts, left in ((self.COUNTS, True), (dict.fromkeys(self.COUNTS, 0), False)):
            with self.subTest(left=left):
                with mock.patch.object(privacy, "owned_data_counts", return_value=counts) as count:
                    body = self._call("get", "/api/admin/me/leftover-data").get_json()
                self.assertEqual((body, count.call_args.args), ({"counts": counts, "any": left}, (1,)))

    def test_wiping_needs_a_recent_password_and_the_typed_word(self):
        cases = [({"confirm": "delete"}, False, 403), ({"confirm": "yes"}, True, 409),
                 ({}, True, 409), ({"confirm": " Delete "}, True, 200)]
        for body, stepped, status in cases:
            with self.subTest(body=body, stepped=stepped):
                self.x.calls.clear()
                with mock.patch.object(privacy, "wipe_owned_data", return_value=self.COUNTS) as wipe:
                    res = self._call("post", "/api/admin/me/leftover-data/wipe", body, stepped_up=stepped)
                self.assertEqual((res.status_code, wipe.called), (status, status == 200))
                if status == 200:
                    self.assertEqual((wipe.call_args.args, res.get_json()["removed"]), ((1,), self.COUNTS))
                    audit = self.x.sql("INSERT INTO audit_log")[0][1]
                    self.assertEqual(audit[1], "admin.leftover_data.wipe")

    def test_a_running_import_stops_the_wipe(self):
        with mock.patch.object(privacy, "wipe_owned_data", side_effect=privacy.EraseError("An import is still running")):
            res = self._call("post", "/api/admin/me/leftover-data/wipe", {"confirm": "delete"}, stepped_up=True)
        self.assertEqual((res.status_code, res.get_json()), (409, {"error": "An import is still running"}))


class WipeCoverageTest(unittest.TestCase):
    def test_every_table_is_either_wiped_or_kept(self):
        every = set(tables.TABLE_ORDER) | set(tables.EXCLUDED)
        classified = set(privacy.WIPED) | set(privacy.WIPED_WITH_PARENT) | set(privacy.KEPT_ON_WIPE)
        self.assertEqual(every - classified, set(), "decide whether wiping an account's data removes these")
        self.assertEqual(classified - every, set(), "these tables do not exist")
        self.assertEqual(len(privacy.WIPED) + len(privacy.WIPED_WITH_PARENT) + len(privacy.KEPT_ON_WIPE),
                         len(classified), "a table is listed twice")
        self.assertTrue(set(privacy.WIPED_WITH_PARENT.values()) <= set(privacy.WIPED))

    def test_children_are_deleted_before_what_they_point_at(self):
        order = privacy.WIPED
        for child, parent in (("transactions", "statements"), ("transactions", "accounts"),
                              ("rules", "categories"), ("budgets", "categories"), ("statements", "accounts")):
            with self.subTest(child=child, parent=parent):
                self.assertLess(order.index(child), order.index(parent))


class OwnAccountTest(_Api):
    def test_signing_out_other_sessions_keeps_this_one(self):
        self.x.routes.append(("UPDATE users SET session_epoch", {"session_epoch": 4}))
        c = self.app.test_client()
        with c.session_transaction() as sess:
            sess["user_id"], sess["role"], sess["uepoch"] = 1, "admin", 3
        with mock.patch.object(FAKE, "query", side_effect=self.q), \
                mock.patch.object(FAKE, "execute", side_effect=self.x), mock.patch.object(util, "db", FAKE):
            res = c.post("/api/admin/me/sign-out-others")
        with c.session_transaction() as sess:
            self.assertEqual((res.status_code, sess["uepoch"]), (200, 4))
        self.assertEqual(self.x.sql("UPDATE users SET session_epoch")[0][1], (1,))

    def test_sign_ins_are_only_your_own(self):
        self._call("get", "/api/admin/me/logins")
        self.assertEqual(self.q.sql("FROM login_events")[0][1], (1,))


class InboxTest(_Api):
    def test_only_past_events_can_be_marked_seen(self):
        with mock.patch.object(admin_metrics, "alerts", return_value=[]) as live:
            ok = self._call("post", "/api/admin/metrics/alerts/errors/dismiss")
            refused = self._call("post", "/api/admin/metrics/alerts/payment_due/dismiss")
        self.assertEqual((ok.status_code, ok.get_json(), refused.status_code), (200, [], 400))
        self.assertIn("alert_seen:errors", FAKE.settings)
        self.assertNotIn("alert_seen:payment_due", FAKE.settings)
        self.assertEqual(live.call_args.args, (1,), "the inbox that comes back is this admin's")

    def test_customers_cannot_see_or_dismiss_the_inbox(self):
        for method, path in (("get", "/api/admin/metrics/alerts"), ("post", "/api/admin/metrics/alerts/errors/dismiss")):
            with self.subTest(path=path):
                self.assertEqual(self._call(method, path, uid=2, role="user").status_code, 403)


class ShellTest(_Api):
    def test_the_stripe_badge_follows_the_configured_key(self):
        import billing
        cases = [(False, "", "off"), (True, "sk_test_abc", "test"), (True, "rk_test_abc", "test"),
                 (True, "sk_live_abc", "live")]
        for enabled, key, mode in cases:
            with self.subTest(key=key):
                with mock.patch.object(billing, "enabled", return_value=enabled), \
                        mock.patch.object(billing, "secret_key", return_value=key):
                    body = self._call("get", "/api/admin/shell").get_json()
                self.assertEqual(body, {"stripe_mode": mode})


class AiConfigTest(_Api):
    MASK = admin_api.AI_MASK

    def test_reading_masks_the_key(self):
        FAKE.settings.update({"openrouter_api_key": "sk-shared", "openrouter_model": "m/shared"})
        self.assertEqual(self._call("get", "/api/admin/ai-config").get_json(),
                         {"api_key": self.MASK, "model": "m/shared"})

    def test_a_new_key_needs_a_recent_password_but_the_model_does_not(self):
        cases = [({"api_key": "sk-new"}, False, 403, {}),
                 ({"api_key": self.MASK, "model": " m/b "}, False, 200, {"openrouter_model": "m/b"}),
                 ({"api_key": " sk-new "}, True, 200, {"openrouter_api_key": "sk-new"}),
                 ({"api_key": ""}, True, 200, {"openrouter_api_key": ""})]
        for body, stepped, status, stored in cases:
            with self.subTest(body=body, stepped=stepped):
                FAKE.settings.clear()
                res = self._call("put", "/api/admin/ai-config", body, stepped_up=stepped)
                self.assertEqual((res.status_code, FAKE.settings), (status, stored))

    def test_the_test_button_uses_the_instance_key_not_a_personal_one(self):
        cases = [({"api_key": self.MASK, "model": "m/a"}, None), ({"api_key": "sk-try", "model": ""}, "sk-try")]
        for body, key in cases:
            with self.subTest(body=body):
                with mock.patch.object(openrouter, "test_connection", return_value={"ok": True}) as test:
                    self._call("post", "/api/admin/ai-config/test", body)
                self.assertEqual(test.call_args.kwargs, {"key": key, "model_id": body["model"] or None})


if __name__ == "__main__":
    unittest.main()
