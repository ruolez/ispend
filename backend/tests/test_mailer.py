import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

import email_templates  # noqa: E402
import mailer  # noqa: E402


class MailerTest(unittest.TestCase):
    def setUp(self):
        FAKE.settings.clear()

    def _configure(self, **over):
        FAKE.settings.update({"smtp_host": "smtp.test", "smtp_from_email": "no-reply@test",
                              "smtp_user": "u", "smtp_password": "p", **over})

    def test_unconfigured_is_a_logged_no_op(self):
        """A fresh install and the dev environment must work untouched."""
        with mock.patch("smtplib.SMTP") as smtp:
            ok, err = mailer.send("a@b.co", "s", "t")
        self.assertEqual((ok, err), (False, "not configured"))
        smtp.assert_not_called()

    def test_starttls_is_the_default_and_login_is_used(self):
        self._configure()
        with mock.patch("smtplib.SMTP") as smtp:
            ok, err = mailer.send("a@b.co", "Subject", "Body")
        self.assertEqual((ok, err), (True, None))
        self.assertEqual(smtp.call_args.args[:2], ("smtp.test", 587))
        server = smtp.return_value.__enter__.return_value
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("u", "p")
        server.send_message.assert_called_once()

    def test_ssl_mode_uses_smtp_ssl_and_port_465(self):
        self._configure(smtp_security="ssl")
        with mock.patch("smtplib.SMTP_SSL") as smtp:
            mailer.send("a@b.co", "s", "t")
        self.assertEqual(smtp.call_args.args[1], 465)

    def test_no_auth_when_no_user_is_set(self):
        self._configure(smtp_user="", smtp_security="none")
        with mock.patch("smtplib.SMTP") as smtp:
            mailer.send("a@b.co", "s", "t")
        smtp.return_value.__enter__.return_value.login.assert_not_called()

    def test_a_failing_relay_never_raises(self):
        self._configure()
        with mock.patch("smtplib.SMTP", side_effect=OSError("connection refused")):
            ok, err = mailer.send("a@b.co", "s", "t")
        self.assertEqual(ok, False)
        self.assertIn("connection refused", err)

    def test_the_message_is_plain_text_first(self):
        self._configure()
        with mock.patch("smtplib.SMTP") as smtp:
            mailer.send("a@b.co", "Subject", "Plain body", "<p>HTML body</p>")
        msg = smtp.return_value.__enter__.return_value.send_message.call_args.args[0]
        parts = [p.get_content_type() for p in msg.walk() if not p.is_multipart()]
        self.assertEqual(parts, ["text/plain", "text/html"])
        self.assertEqual(msg["Auto-Submitted"], "auto-generated")

    def test_addresses_are_redacted_in_the_audit_trail(self):
        """The audit log must not become a mailing list."""
        self.assertEqual(mailer._redact("someone@example.com"), "***@example.com")
        self.assertEqual(mailer._redact(""), "***")


class TemplateTest(unittest.TestCase):
    def test_every_template_renders(self):
        for name in email_templates.TEMPLATES:
            with self.subTest(template=name):
                subject, text, html = email_templates.render(
                    name, username="amy", link="https://x/y", trial_end="1 July", days=3, until="8 July")
                self.assertTrue(subject and text.strip())
                self.assertIn("<html>", html)
                self.assertNotIn("{", text)

    def test_interpolated_values_are_escaped_in_the_html_part(self):
        _, text, html = email_templates.render("verify_email", username="<script>x</script>",
                                               link="https://x/y?a=1&b=2")
        self.assertIn("<script>", text)                 # plain text is untouched
        self.assertNotIn("<script>", html)              # the HTML alternative is escaped
        self.assertIn("&amp;b=2", html)

    def test_an_unknown_template_raises(self):
        with self.assertRaises(KeyError):
            email_templates.render("nope")


if __name__ == "__main__":
    unittest.main()
