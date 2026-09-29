import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import admin_billing  # noqa: E402
import admin_support  # noqa: E402
import config  # noqa: E402
import mailer  # noqa: E402
import support_api  # noqa: E402
import util  # noqa: E402

NOW = datetime.now(timezone.utc)
ADMIN = 1
CUSTOMER = 3


def loaded(**over):
    row = {"id": 42, "user_id": CUSTOMER, "kind": "bug", "subject": "Import stuck", "status": "open", "impact": "blocking",
           "priority": None, "created_at": NOW, "updated_at": NOW, "last_user_at": NOW, "last_admin_at": None,
           "user_seen_at": NOW, "admin_seen_at": None, "resolved_at": None,
           "context": {"page": "/import.html", "requests": [{"request_id": "abcdef012345", "status": 500}]},
           "username": "amy", "email": "amy@example.com", "user_status": "active", "user_created_at": NOW}
    return {**row, **over}


class AdminSupportTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(admin_support.bp)
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)
        self.sent = []
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir = config.STATEMENTS_DIR
        config.STATEMENTS_DIR = self.tmp.name
        FAKE.settings.clear()

    def tearDown(self):
        config.STATEMENTS_DIR = self.old_dir
        self.tmp.cleanup()

    def _call(self, method, path, body=None, role="admin", data=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"], s["role"] = (ADMIN if role == "admin" else CUSTOMER), role
        kwargs = {}
        if data is not None:
            kwargs = {"data": data, "content_type": "multipart/form-data"}
        elif body is not None:
            kwargs = {"data": json.dumps(body), "content_type": "application/json"}
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(admin_support, "db", FAKE), \
                mock.patch.object(support_api, "db", FAKE), \
                mock.patch.object(admin_billing, "users_block", return_value={CUSTOMER: {"state": "active"}}), \
                mock.patch.object(mailer, "send_async", side_effect=lambda t, to, **kw: self.sent.append((t, to, kw))), \
                mock.patch.object(support_api, "base_url", return_value="https://app.test"):
            return getattr(c, method)(path, **kwargs)

    def test_customers_are_kept_out(self):
        for method, path in (("get", "/api/admin/support/reports"), ("get", "/api/admin/support/reports/42"),
                             ("post", "/api/admin/support/reports/42/messages"), ("patch", "/api/admin/support/reports/42"),
                             ("get", "/api/admin/support/settings"), ("put", "/api/admin/support/settings")):
            with self.subTest(path=path):
                self.assertEqual(self._call(method, path, {}, role="user").status_code, 403)
        self.assertEqual((self.q.calls, self.x.calls), ([], []))

    # ---- the inbox ----

    def test_views_filter_by_whose_move_it_is_and_count_every_tab(self):
        self.q.routes += [("COUNT(*) FILTER", {"needs_reply": 2, "waiting": 1, "resolved": 5, "all": 8}),
                          ("ORDER BY", [{**loaded(), "message_count": 1, "attachment_count": 1, "preview": "spins"}])]
        body = self._call("get", "/api/admin/support/reports?view=waiting&kind=bug&priority=1").get_json()
        self.assertEqual(body["counts"], {"needs_reply": 2, "waiting": 1, "resolved": 5, "all": 8})
        self.assertEqual([(r["ref"], r["user"]["username"], r["unread"]) for r in body["reports"]], [("R-1042", "amy", True)])
        sql, params = self.q.sql("ORDER BY")[0]
        self.assertIn("r.status = 'waiting' AND r.kind = %(kind)s AND r.priority = %(priority)s", sql)
        self.assertEqual((params["kind"], params["priority"]), ("bug", 1))

    def test_searching_a_reference_finds_that_report(self):
        self.q.routes += [("COUNT(*) FILTER", {})]
        self._call("get", "/api/admin/support/reports?view=all&q=R-1042")
        sql, params = self.q.sql("ORDER BY")[0]
        self.assertIn("r.id = %(rid)s", sql)
        self.assertEqual(params["rid"], 42)

    def test_an_unknown_view_is_refused(self):
        self.assertEqual(self._call("get", "/api/admin/support/reports?view=spam").status_code, 400)

    # ---- a thread ----

    def test_the_thread_has_notes_the_customer_and_matching_server_errors(self):
        self.q.routes += [
            ("FROM support_reports r JOIN users u ON u.id = r.user_id WHERE r.id", loaded()),
            ("FROM app_errors", [{"id": 1, "error_type": "KeyError", "message": "x", "location": "POST /api/statements",
                                  "status": 500, "request_id": "abcdef012345", "created_at": NOW},
                                 {"id": 2, "error_type": "ValueError", "message": "y", "location": "GET /api/x",
                                  "status": 500, "request_id": "999999999999", "created_at": NOW}]),
            ("FROM support_messages m LEFT JOIN users", [
                {"id": 7, "author_role": "user", "internal": False, "body": "spins", "created_at": NOW, "author": "amy"},
                {"id": 8, "author_role": "admin", "internal": True, "body": "OCR timeout?", "created_at": NOW, "author": "eugene"}]),
        ]
        body = self._call("get", "/api/admin/support/reports/42").get_json()
        self.assertEqual([(m["id"], m["internal"]) for m in body["messages"]], [(7, False), (8, True)])
        self.assertNotIn("AND NOT m.internal", self.q.sql("FROM support_messages m LEFT JOIN users")[0][0])
        self.assertEqual([(e["id"], e["matches_report"]) for e in body["server_errors"]], [(1, True), (2, False)])
        self.assertEqual(self.q.sql("FROM app_errors")[0][1]["ids"], ["abcdef012345"])
        self.assertEqual((body["customer"]["username"], body["customer"]["billing"]), ("amy", {"state": "active"}))
        self.assertEqual(self.x.sql("SET admin_seen_at = now()")[0][1], (42,))

    def test_a_missing_report_is_not_found(self):
        for method, path in (("get", "/api/admin/support/reports/9"), ("post", "/api/admin/support/reports/9/messages"),
                             ("patch", "/api/admin/support/reports/9"), ("get", "/api/admin/support/attachments/9")):
            with self.subTest(path=path):
                self.assertEqual(self._call(method, path, {"body": "hi", "status": "open"}).status_code, 404)

    # ---- answering ----

    def _thread_routes(self, **over):
        self.q.routes += [("FROM support_reports r JOIN users u ON u.id = r.user_id WHERE r.id", loaded(**over))]
        self.x.routes += [("INSERT INTO support_messages", {"id": 9})]

    def test_a_reply_waits_on_the_customer_and_emails_them(self):
        self._thread_routes()
        res = self._call("post", "/api/admin/support/reports/42/messages", {"body": "Could you try again now?"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.x.sql("INSERT INTO support_messages")[0][1], (42, ADMIN, "admin", False, "Could you try again now?"))
        self.assertEqual(self.x.sql("SET status = %s")[0][1], ("waiting", "waiting", 42))
        self.assertEqual([(t, to, kw["message"], kw["link"]) for t, to, kw in self.sent],
                         [("support_reply", "amy@example.com", "Could you try again now?", "https://app.test/help.html?report=42")])

    def test_reply_and_resolve_sends_the_resolved_email(self):
        self._thread_routes()
        self._call("post", "/api/admin/support/reports/42/messages", {"body": "Fixed in 1.0.1", "resolve": True})
        self.assertEqual(self.x.sql("SET status = %s")[0][1], ("resolved", "resolved", 42))
        self.assertEqual([t for t, _, _ in self.sent], ["support_resolved"])

    def test_a_note_changes_nothing_the_customer_can_see(self):
        self._thread_routes(status="in_progress")
        self._call("post", "/api/admin/support/reports/42/messages", {"body": "Probably OCR", "internal": "1", "resolve": "1"})
        self.assertEqual(self.x.sql("INSERT INTO support_messages")[0][1], (42, ADMIN, "admin", True, "Probably OCR"))
        self.assertEqual((self.x.sql("SET status"), self.x.sql("last_admin_at"), self.sent), ([], [], []))

    def test_an_admin_can_attach_a_screenshot(self):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (8, 8)).save(buf, format="PNG")
        self._thread_routes()
        res = self._call("post", "/api/admin/support/reports/42/messages",
                         data={"body": "Like this", "files": [(io.BytesIO(buf.getvalue()), "x.png")]})
        self.assertEqual(res.status_code, 200)
        params = self.x.sql("INSERT INTO support_attachments")[0][1]
        self.assertEqual((params[:3], params[4]), ((9, 42, CUSTOMER), "image/png"))
        self.assertRegex(params[3], rf"^{CUSTOMER}/support/")

    def test_status_and_priority(self):
        self._thread_routes()
        res = self._call("patch", "/api/admin/support/reports/42", {"status": "resolved", "priority": 1})
        self.assertEqual(res.status_code, 200)
        sql, params = self.x.sql("UPDATE support_reports SET status")[0]
        self.assertEqual(params, ("resolved", "resolved", 1, 42))
        self.assertEqual([t for t, _, _ in self.sent], ["support_resolved"])
        for body, message in (({"status": "closed"}, "Invalid status"), ({"priority": 7}, "Priority must be 1, 2, 3 or empty"),
                              ({"priority": True}, "Priority must be 1, 2, 3 or empty"), ({}, "Nothing to change")):
            with self.subTest(body=body):
                res = self._call("patch", "/api/admin/support/reports/42", body)
                self.assertEqual((res.status_code, res.get_json()["error"]), (400, message))

    def test_clearing_priority_does_not_email(self):
        self._thread_routes(status="resolved", resolved_at=NOW - timedelta(days=1))
        self._call("patch", "/api/admin/support/reports/42", {"priority": None, "status": "resolved"})
        self.assertEqual(self.sent, [])

    # ---- settings ----

    def test_settings_round_trip_and_validation(self):
        self.q.routes += [("WHERE role = 'admin'", [{"id": ADMIN, "username": "admin", "email": "ops@example.com"}])]
        replies = [{"title": "  Fixed ", "body": "This is fixed now.\r\n"}]
        body = self._call("put", "/api/admin/support/settings",
                          {"support_email": "help@ispend.test", "notify": False, "saved_replies": replies}).get_json()
        self.assertEqual(body, {"support_email": "help@ispend.test", "notify": False,
                                "saved_replies": [{"title": "Fixed", "body": "This is fixed now."}],
                                "notify_to": ["ops@example.com"]})
        for payload, message in (({"support_email": "nope"}, "Enter a valid email address"),
                                 ({"saved_replies": [{"title": "", "body": "x"}]}, "Each saved reply needs a name and a message"),
                                 ({"saved_replies": [{"title": "t", "body": "b"}] * 31}, "Keep at most 30 saved replies")):
            with self.subTest(payload=payload):
                res = self._call("put", "/api/admin/support/settings", payload)
                self.assertEqual((res.status_code, res.get_json()["error"]), (400, message))


if __name__ == "__main__":
    unittest.main()
