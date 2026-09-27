"""Admin › Messages: rendering, who receives what, step-up, unsubscribe and sending."""
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

import admin_email  # noqa: E402
import email_templates  # noqa: E402
import mailer  # noqa: E402
import public_api  # noqa: E402

TRIAL_END = datetime(2026, 10, 1, tzinfo=timezone.utc)


def person(uid, **over):
    return {"id": uid, "username": f"p{uid}", "email": f"p{uid}@example.com", "status": "active",
            "email_verified_at": TRIAL_END, "trial_end": TRIAL_END, "suppressed": False, **over}


class RenderTest(unittest.TestCase):
    def test_placeholders_and_braces(self):
        ctx = {"name": "Amy", "trial_end": "1 October 2026", "email": "a@x.io", "app_link": "https://ispend.app"}
        cases = [("Hi {name}, your trial ends {trial_end}.", "Hi Amy, your trial ends 1 October 2026."),
                 ("Use {curly} braces {} freely", "Use {curly} braces {} freely"),
                 ("{password} {__class__}", "{password} {__class__}"),
                 ("Open {app_link}", "Open https://ispend.app")]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(email_templates.fill(text, ctx), expected)

    def test_the_admin_s_own_text_is_escaped_in_html(self):
        subject, text, html = email_templates.render_custom("Hi {name}", "<b>bold</b> & more", {"name": "<Amy>"}, "Footer")
        self.assertEqual(subject, "Hi <Amy>")
        self.assertIn("<b>bold</b>", text)
        self.assertNotIn("<b>bold</b>", html)
        self.assertIn("&lt;b&gt;bold&lt;/b&gt; &amp; more", html)
        self.assertIn("--\nFooter", text)


class TokenTest(unittest.TestCase):
    def test_round_trip_and_tampering(self):
        token = admin_email.unsubscribe_token(42)
        uid, sig = token.split(".")
        self.assertEqual(admin_email.user_for_token(token), 42)
        for bad in (f"43.{sig}", f"42.{sig[:-1]}0", "42", "x.y", "", None):
            with self.subTest(bad=bad):
                self.assertIsNone(admin_email.user_for_token(bad))


class ResolveTest(unittest.TestCase):
    def test_who_receives_what(self):
        rows = [person(1), person(2, email=None), person(3, status="locked"), person(4, suppressed=True),
                person(5, email_verified_at=None)]
        with mock.patch.object(FAKE, "query", return_value=rows):
            service, s_skipped = admin_email.resolve({"ids": [1, 2, 3, 4, 5]}, "service")
            news, n_skipped = admin_email.resolve({"ids": [1, 2, 3, 4, 5]}, "marketing")
        self.assertEqual(([r["id"] for r in service], s_skipped), ([1, 4, 5], {"no_email": 1, "not_active": 1}))
        self.assertEqual(([r["id"] for r in news], n_skipped),
                         ([1], {"no_email": 1, "not_active": 1, "unsubscribed": 1, "unconfirmed": 1}))


class _Api(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "t"
        self.app.register_blueprint(admin_email.bp)
        self.app.register_blueprint(public_api.bp)
        FAKE.settings.clear()
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None, stepped_up=False, form=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"], s["role"] = 1, "admin"
            if stepped_up:
                s["stepup_until"] = time.time() + 600
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(admin_email, "start") as start:
            self.start = start
            if form is not None:
                return getattr(c, method)(path, data=form)
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None,
                                      content_type="application/json")


class SendTest(_Api):
    MSG = {"subject": "Hello {name}", "body": "News", "category": "service"}

    def test_validation(self):
        for body, message in (({**self.MSG, "subject": ""}, "A subject is 1 to 200 characters"),
                              ({**self.MSG, "body": " "}, "Write a message first"),
                              ({**self.MSG, "category": "spam"}, "Choose whether this is about their account or news")):
            with self.subTest(body=body):
                res = self._call("post", "/api/admin/email/send", {**body, "audience": {"ids": [1]}})
                self.assertEqual((res.status_code, res.get_json()["error"]), (400, message))

    def test_sending_to_many_needs_step_up_and_one_does_not(self):
        self.q.routes.append(("FROM users u LEFT JOIN subscriptions", [person(1), person(2)]))
        res = self._call("post", "/api/admin/email/send", {**self.MSG, "audience": {"ids": [1, 2]}})
        self.assertEqual((res.status_code, res.get_json()["code"]), (403, "step_up_required"))
        self.x.routes.insert(0, ("INSERT INTO email_campaigns", {"id": 7}))
        res = self._call("post", "/api/admin/email/send", {**self.MSG, "audience": {"ids": [1, 2]}}, stepped_up=True)
        self.assertEqual((res.status_code, res.get_json()["recipients"]), (202, 2))
        self.start.assert_called_once_with(7)
        self.q.routes[-1] = ("FROM users u LEFT JOIN subscriptions", [person(1)])
        res = self._call("post", "/api/admin/users/1/email", self.MSG)
        self.assertEqual(res.status_code, 202)

    def test_one_person_who_cannot_receive_is_told_why(self):
        self.q.routes.append(("FROM users u LEFT JOIN subscriptions", [person(1, suppressed=True)]))
        res = self._call("post", "/api/admin/users/1/email", {**self.MSG, "category": "marketing"})
        self.assertEqual((res.status_code, res.get_json()["error"]),
                         (409, "They unsubscribed from news; send it as an account message if it is one"))

    def test_preview_shows_the_first_message_filled_in(self):
        self.q.routes.append(("FROM users u LEFT JOIN subscriptions", [person(1), person(2, email=None)]))
        body = self._call("post", "/api/admin/email/preview", {**self.MSG, "audience": {"ids": [1, 2]}}).get_json()
        self.assertEqual((body["recipients"], body["skipped"], body["sample"]["subject"]), (1, {"no_email": 1}, "Hello p1"))

    def test_an_email_username_is_greeted_by_its_first_part(self):
        self.assertEqual(admin_email._context({"username": "amy.lee@example.com"})["name"], "amy.lee")


class RunTest(unittest.TestCase):
    def test_news_carries_one_click_unsubscribe_and_cancel_stops(self):
        claims = [{"id": 11, "user_id": 1, "to_address": "p1@example.com"}, {"id": 12, "user_id": 2, "to_address": "p2@example.com"}]
        statuses = iter(["sending", "sending", "sending", "canceled"])
        q = _stubs.Router([("SELECT * FROM email_campaigns", {"id": 3, "status": "sending", "subject": "News", "body": "Hi {name}",
                                                             "category": "marketing"}),
                           ("SELECT status FROM email_campaigns", lambda s, p: {"status": next(statuses)}),
                           ("FROM users u", lambda s, p: person(p[0]))])
        x = _stubs.Router([("status = 'sending', sent_at = now()", lambda s, p: claims.pop(0) if claims else None)], default=1)
        with mock.patch.object(FAKE, "query", side_effect=q), mock.patch.object(FAKE, "execute", side_effect=x), \
                mock.patch.object(mailer, "_deliver", return_value=(True, None, "<m>")) as deliver:
            sent = admin_email.run(3, sleep=lambda s: None)
        self.assertEqual(sent, 2)
        headers = deliver.call_args_list[0].kwargs["headers"]
        self.assertEqual(headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertIn("/api/public/unsubscribe?t=1.", headers["List-Unsubscribe"])
        self.assertIn("Unsubscribe with one click", deliver.call_args_list[0].args[2])
        self.assertEqual(len(x.sql("UPDATE email_log SET status = %s")), 2)


class UnsubscribeTest(_Api):
    def test_a_link_scanner_cannot_unsubscribe_but_the_button_can(self):
        token = admin_email.unsubscribe_token(5)
        self.q.routes.append(("SELECT email FROM users", {"email": "p5@example.com"}))
        res = self._call("get", f"/api/public/unsubscribe?t={token}")
        self.assertEqual((res.status_code, self.x.sql("email_suppressions")), (200, []))
        self.assertIn("Unsubscribe from news", res.get_data(as_text=True))
        res = self._call("post", f"/api/public/unsubscribe?t={token}", form={"List-Unsubscribe": "One-Click"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.x.sql("email_suppressions")[0][1], (admin_email.email_hash("p5@example.com"),))

    def test_a_forged_link_changes_nothing(self):
        res = self._call("post", "/api/public/unsubscribe?t=5.deadbeef", form={})
        self.assertEqual((res.status_code, self.x.sql("email_suppressions")), (404, []))


if __name__ == "__main__":
    unittest.main()
