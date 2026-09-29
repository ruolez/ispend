"""A problem report's whole life against a real database: filed with a screenshot, answered,
reopened, resolved, exported and erased.

    ISPEND_TEST_DSN=postgresql://... python -m unittest tests.test_support_integration
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _pg  # noqa: E402

BODY = "The import page spins forever after I pick a PDF."


def png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (30, 20), (9, 9, 9)).save(buf, format="PNG")
    return buf.getvalue()


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class SupportLifecycleTest(_pg.PgTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import support_api
        cls.app.register_blueprint(support_api.bp)
        cls.amy = cls.db.execute("INSERT INTO users (username, email, password_hash) VALUES ('amy', 'amy@example.com', 'x') RETURNING id",
                                 returning=True)["id"]
        cls.bob = cls.db.execute("INSERT INTO users (username, password_hash) VALUES ('bob', 'x') RETURNING id",
                                 returning=True)["id"]
        cls.root = cls.db.query("SELECT id FROM users WHERE username = 'admin'", one=True)["id"]
        cls.db.execute("UPDATE users SET email = 'ops@example.com' WHERE id = %s", (cls.root,))

    def setUp(self):
        self.sent = []
        patcher = mock.patch("mailer.send_async", side_effect=lambda t, to, **kw: self.sent.append((t, to)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _client(self, uid, role="user"):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = uid
            s["role"] = role
        return c

    def test_the_whole_thread(self):
        amy = self._client(self.amy)
        res = amy.post("/api/support/reports", content_type="multipart/form-data", data={
            "kind": "bug", "body": BODY, "impact": "blocking",
            "context": json.dumps({"page": "/import.html", "requests": [{"method": "POST", "path": "/api/statements/9",
                                                                        "status": 500, "request_id": "abcdef012345"}]}),
            "files": [(io.BytesIO(png()), "shot.png")]})
        self.assertEqual(res.status_code, 201, res.get_json())
        report = res.get_json()
        rid = report["id"]
        self.assertEqual((report["status"], len(report["messages"]), len(report["messages"][0]["attachments"])),
                         ("open", 1, 1))
        self.assertEqual(self.sent, [("support_received", "amy@example.com"), ("support_admin_new", "ops@example.com")])
        stored = self.db.query("SELECT context FROM support_reports WHERE id = %s", (rid,), one=True)["context"]
        self.assertEqual(stored["requests"], [{"method": "POST", "path": "/api/statements/:id", "status": 500,
                                               "request_id": "abcdef012345"}])

        shot = amy.get(report["messages"][0]["attachments"][0]["url"])
        self.assertEqual((shot.status_code, shot.mimetype, shot.data[:4]), (200, "image/png", b"\x89PNG"))
        shot.close()

        # someone else sees nothing
        bob = self._client(self.bob)
        self.assertEqual([bob.get(f"/api/support/reports/{rid}").status_code,
                          bob.get(report["messages"][0]["attachments"][0]["url"]).status_code,
                          len(bob.get("/api/support/reports").get_json())], [404, 404, 0])

        # the operator answers and leaves a note; the note never reaches the customer
        self.db.execute("""INSERT INTO support_messages (report_id, author_id, author_role, internal, body)
                           VALUES (%s, %s, 'admin', true, 'Looks like the OCR timeout'),
                                  (%s, %s, 'admin', false, 'Could you try again now?')""",
                        (rid, self.root, rid, self.root))
        self.db.execute("UPDATE support_reports SET status = 'waiting', last_admin_at = now() WHERE id = %s", (rid,))
        listed = amy.get("/api/support/reports").get_json()
        self.assertEqual([(r["id"], r["unread"], r["message_count"]) for r in listed], [(rid, True, 2)])
        thread = amy.get(f"/api/support/reports/{rid}").get_json()
        self.assertEqual([m["body"] for m in thread["messages"]], [BODY, "Could you try again now?"])
        self.assertEqual(amy.get("/api/support/reports").get_json()[0]["unread"], False)

        # answering puts it back in the queue; resolving closes it; a later answer reopens it
        self.assertEqual(amy.post(f"/api/support/reports/{rid}/messages", json={"body": "Works now, thanks"}).get_json()["status"], "open")
        self.assertEqual(amy.post(f"/api/support/reports/{rid}/resolve").get_json()["status"], "resolved")
        again = amy.post(f"/api/support/reports/{rid}/messages", json={"body": "It broke again"}).get_json()
        self.assertEqual((again["status"], again["resolved_at"]), ("open", None))

        # a long-resolved report stays closed
        self.db.execute("UPDATE support_reports SET status = 'resolved', resolved_at = now() - interval '31 days' WHERE id = %s", (rid,))
        self.assertEqual(amy.post(f"/api/support/reports/{rid}/messages", json={"body": "Hello?"}).status_code, 409)

        # the export carries the conversation but not the operator's note
        import privacy
        with self.app.test_request_context():
            msgs = self.db.query(privacy.EXPORTED["support_messages"], {"u": self.amy})
        self.assertEqual([m["body"] for m in msgs], [BODY, "Could you try again now?", "Works now, thanks", "It broke again"])

        # erasing the account removes the report and its screenshot rows
        path = self.db.query("SELECT stored_path FROM support_attachments WHERE report_id = %s", (rid,), one=True)["stored_path"]
        self.db.execute("DELETE FROM users WHERE id = %s", (self.amy,))
        self.assertEqual(self.db.query("SELECT COUNT(*) AS n FROM support_attachments WHERE stored_path = %s", (path,), one=True)["n"], 0)
