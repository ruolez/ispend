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
from PIL import Image  # noqa: E402

import config  # noqa: E402
import mailer  # noqa: E402
import support  # noqa: E402
import support_api  # noqa: E402
import util  # noqa: E402

NOW = datetime.now(timezone.utc)
UID = 3
BODY = "The import page spins forever after I pick a PDF."
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"


def report_row(**over):
    row = {"id": 42, "user_id": UID, "kind": "bug", "subject": "Import stuck", "status": "open", "impact": None,
           "priority": None, "created_at": NOW, "updated_at": NOW, "last_user_at": NOW, "last_admin_at": None,
           "user_seen_at": NOW, "admin_seen_at": None, "resolved_at": None}
    return {**row, **over}


def png():
    buf = io.BytesIO()
    Image.new("RGB", (20, 10), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


class SupportApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(support_api.bp)
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)
        self.tmp = tempfile.TemporaryDirectory()
        self.old_dir = config.STATEMENTS_DIR
        config.STATEMENTS_DIR = self.tmp.name
        self.sent = []
        FAKE.settings.clear()

    def tearDown(self):
        config.STATEMENTS_DIR = self.old_dir
        self.tmp.cleanup()

    def _call(self, method, path, json_body=None, data=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = UID
        kwargs = {"headers": {"User-Agent": UA}}
        if data is not None:
            kwargs.update(data=data, content_type="multipart/form-data")
        elif json_body is not None:
            kwargs.update(data=json.dumps(json_body), content_type="application/json")
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(support_api, "db", FAKE), \
                mock.patch.object(mailer, "send_async", side_effect=lambda t, to, **kw: self.sent.append((t, to, kw))), \
                mock.patch.object(support_api, "base_url", return_value="https://app.test"):
            return getattr(c, method)(path, **kwargs)

    def _stored_files(self):
        root = os.path.join(self.tmp.name, str(UID), "support")
        return sorted(os.listdir(root)) if os.path.isdir(root) else []

    # ---- creating ----

    def test_an_invalid_report_writes_nothing(self):
        res = self._call("post", "/api/support/reports", {"kind": "bug", "body": "short"})
        self.assertEqual((res.status_code, res.get_json()["error"]),
                         (400, f"Add a little more detail (at least {support.BODY_MIN} characters)"))
        self.assertEqual([c for c in self.x.calls if "INSERT" in c[0]], [])

    def test_a_report_with_a_screenshot_is_stored_acknowledged_and_announced(self):
        FAKE.settings["support_notify"] = "1"
        self.q.routes += [
            ("COUNT(*) AS n FROM support_reports", {"n": 0}),
            ("FROM support_reports r WHERE r.id", report_row()),
            ("FROM users WHERE id", {"id": UID, "username": "amy", "email": "amy@example.com"}),
            ("WHERE role = 'admin'", [{"id": 1, "username": "admin", "email": "ops@example.com"}]),
        ]
        self.x.routes += [("INSERT INTO support_reports", report_row()), ("INSERT INTO support_messages", {"id": 7})]
        context = {"page": "/import.html?x=1", "theme": "dark", "cookies": "nope"}
        res = self._call("post", "/api/support/reports", data={
            "kind": "bug", "body": BODY, "impact": "blocking", "context": json.dumps(context),
            "files": [(io.BytesIO(png()), "shot.png")]})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.get_json()["ref"], "R-1042")
        params = self.x.sql("INSERT INTO support_reports")[0][1]
        self.assertEqual(params[:4], (UID, "bug", support.subject_from(BODY), "blocking"))
        self.assertEqual(json.loads(params[4]), {"page": "/import.html", "theme": "dark",
                                                 "browser": "Safari 18", "os": "iOS", "device": "mobile"})
        self.assertEqual(self.x.sql("INSERT INTO support_messages")[0][1], (42, UID, "user", False, BODY))
        att = self.x.sql("INSERT INTO support_attachments")[0][1]
        self.assertEqual((att[:3], att[4:]), ((7, 42, UID), ("image/png", att[5], 20, 10)))
        self.assertRegex(att[3], rf"^{UID}/support/[0-9a-f]{{32}}\.png$")
        self.assertEqual(self._stored_files(), [os.path.basename(att[3])])
        self.assertEqual([(t, to) for t, to, _ in self.sent],
                         [("support_received", "amy@example.com"), ("support_admin_new", "ops@example.com")])
        self.assertEqual(self.sent[0][2]["link"], "https://app.test/help.html?report=42")
        self.assertEqual(self.sent[1][2]["link"], "https://app.test/admin#support/42")

    def test_turning_off_notifications_emails_only_the_customer(self):
        FAKE.settings["support_notify"] = "0"
        self.q.routes += [("COUNT(*) AS n FROM support_reports", {"n": 0}),
                          ("FROM support_reports r WHERE r.id", report_row()),
                          ("FROM users WHERE id", {"id": UID, "username": "amy", "email": "amy@example.com"})]
        self.x.routes += [("INSERT INTO support_reports", report_row()), ("INSERT INTO support_messages", {"id": 7})]
        self._call("post", "/api/support/reports", {"kind": "question", "body": BODY})
        self.assertEqual([t for t, _, _ in self.sent], ["support_received"])

    def test_without_technical_details_nothing_is_captured(self):
        self.q.routes += [("COUNT(*) AS n FROM support_reports", {"n": 0}),
                          ("FROM support_reports r WHERE r.id", report_row())]
        self.x.routes += [("INSERT INTO support_reports", report_row()), ("INSERT INTO support_messages", {"id": 7})]
        self._call("post", "/api/support/reports", {"kind": "question", "body": BODY})
        self.assertEqual(self.x.sql("INSERT INTO support_reports")[0][1][4], "{}")

    def test_the_daily_limit_stops_a_flood(self):
        self.q.routes += [("COUNT(*) AS n FROM support_reports", {"n": support_api.REPORTS_PER_DAY})]
        res = self._call("post", "/api/support/reports", {"kind": "bug", "body": BODY})
        self.assertEqual(res.status_code, 429)
        self.assertEqual(self.x.sql("INSERT INTO support_reports"), [])

    def test_a_file_that_is_not_an_image_is_refused_before_anything_is_written(self):
        self.q.routes += [("COUNT(*) AS n FROM support_reports", {"n": 0})]
        res = self._call("post", "/api/support/reports", data={
            "kind": "bug", "body": BODY, "files": [(io.BytesIO(b"%PDF-1.7 statement"), "shot.png")]})
        self.assertEqual((res.status_code, res.get_json()["error"]), (400, "Screenshots must be PNG, JPEG or WebP images"))
        self.assertEqual((self.x.sql("INSERT"), self._stored_files()), ([], []))

    def test_a_failed_database_write_removes_the_stored_screenshot(self):
        self.q.routes += [("COUNT(*) AS n FROM support_reports", {"n": 0})]

        def boom(sql, params):
            raise RuntimeError("db down")
        self.x.routes += [("INSERT INTO support_reports", report_row()), ("INSERT INTO support_messages", boom)]
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        with self.assertRaises(RuntimeError):
            self._call("post", "/api/support/reports", data={
                "kind": "bug", "body": BODY, "files": [(io.BytesIO(png()), "a.png")]})
        self.assertEqual(self._stored_files(), [])

    # ---- reading ----

    def test_another_customers_report_is_not_found(self):
        for method, path in (("get", "/api/support/reports/42"), ("post", "/api/support/reports/42/messages"),
                             ("post", "/api/support/reports/42/resolve"), ("get", "/api/support/attachments/9")):
            with self.subTest(path=path):
                res = self._call(method, path, {"body": "hello there"})
                self.assertEqual(res.status_code, 404)
        for _sql, params in self.q.calls:
            self.assertIn(UID, params)

    def test_the_thread_hides_internal_notes_and_marks_the_report_seen(self):
        self.q.routes += [
            ("FROM support_reports r WHERE r.id", report_row(status="waiting", last_admin_at=NOW)),
            ("FROM support_messages m LEFT JOIN users", [
                {"id": 7, "author_role": "user", "internal": False, "body": BODY, "created_at": NOW, "author": "amy"},
                {"id": 8, "author_role": "admin", "internal": False, "body": "Fixed", "created_at": NOW, "author": "eugene"}]),
            ("FROM support_attachments a", [{"id": 5, "message_id": 7, "mime": "image/png", "bytes": 10, "width": 2, "height": 1}]),
        ]
        body = self._call("get", "/api/support/reports/42").get_json()
        self.assertEqual([(m["id"], m["author"], len(m["attachments"])) for m in body["messages"]],
                         [(7, None, 1), (8, "eugene", 0)])
        self.assertEqual(body["messages"][0]["attachments"][0]["url"], "/api/support/attachments/5")
        for needle in ("FROM support_messages m LEFT JOIN users", "FROM support_attachments a"):
            self.assertIn("AND NOT m.internal", self.q.sql(needle)[0][0])
        self.assertEqual(self.x.sql("SET user_seen_at = now()")[0][1], (42, UID))

    def test_the_list_flags_unread_replies(self):
        earlier = NOW - timedelta(hours=1)
        self.q.routes += [("FROM support_reports r WHERE r.user_id", [
            report_row(id=1, last_admin_at=NOW, user_seen_at=earlier, message_count=2),
            report_row(id=2, last_admin_at=earlier, user_seen_at=NOW, message_count=2),
            report_row(id=3, message_count=1)])]
        rows = self._call("get", "/api/support/reports").get_json()
        self.assertEqual([(r["ref"], r["unread"]) for r in rows], [("R-1001", True), ("R-1002", False), ("R-1003", False)])

    # ---- replying ----

    def _reply_routes(self, report):
        self.q.routes += [("FROM support_reports r WHERE r.id", report), ("FROM support_messages m JOIN", {"n": 0}),
                          ("FROM users WHERE id", {"id": UID, "username": "amy", "email": None})]
        self.x.routes += [("INSERT INTO support_messages", {"id": 9})]

    def test_answering_a_question_puts_it_back_in_the_operators_queue(self):
        self._reply_routes(report_row(status="waiting"))
        res = self._call("post", "/api/support/reports/42/messages", {"body": "Still broken on my phone"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.x.sql("UPDATE support_reports SET status")[0][1], ("open", "open", 42, UID))
        self.assertEqual(self.x.sql("INSERT INTO support_messages")[0][1], (42, UID, "user", False, "Still broken on my phone"))

    def test_a_long_resolved_report_asks_for_a_new_one(self):
        old = NOW - timedelta(days=support.REOPEN_DAYS + 1)
        self._reply_routes(report_row(status="resolved", resolved_at=old))
        res = self._call("post", "/api/support/reports/42/messages", {"body": "It is back"})
        self.assertEqual(res.status_code, 409)
        self.assertIn("Start a new report and mention R-1042", res.get_json()["error"])
        self.assertEqual(self.x.sql("INSERT"), [])

    def test_resolving_is_idempotent(self):
        self.q.routes += [("FROM support_reports r WHERE r.id", report_row(status="resolved", resolved_at=NOW))]
        res = self._call("post", "/api/support/reports/42/resolve")
        self.assertEqual((res.status_code, self.x.sql("UPDATE support_reports")), (200, []))

    def test_meta_reports_version_and_contact(self):
        FAKE.settings["support_email"] = " help@ispend.test "
        body = self._call("get", "/api/support/meta").get_json()
        self.assertEqual((body["support_email"], body["max_files"], body["version"]),
                         ("help@ispend.test", 3, support_api.app_version()))
        self.assertRegex(body["version"], r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
