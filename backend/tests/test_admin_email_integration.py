"""Messages against a real database: suppression lookup, claiming rows, counters.

    ISPEND_TEST_DSN=postgresql://... python -m unittest tests.test_admin_email_integration
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _pg  # noqa: E402


@unittest.skipUnless(_pg.available(), "set ISPEND_TEST_DSN and run this file on its own")
class CampaignTest(_pg.PgTestCase):
    def test_news_skips_the_unsubscribed_and_everyone_else_gets_one_message(self):
        import admin_email
        import mailer
        db = self.db
        ids = [db.execute("""INSERT INTO users (username, email, password_hash, email_verified_at)
                             VALUES (%s, %s, 'x', now()) RETURNING id""", (n, f"{n}@example.com"), returning=True)["id"]
               for n in ("ann", "ben", "cy")]
        db.execute("INSERT INTO email_suppressions (email_sha256, reason) VALUES (%s, 'unsubscribed')",
                   (admin_email.email_hash("BEN@example.com"),))
        with self.app.test_request_context():
            from flask import session
            session["user_id"] = ids[0]
            recipients, skipped = admin_email.resolve({"ids": ids}, "marketing")
            self.assertEqual(([r["username"] for r in sorted(recipients, key=lambda r: r["id"])], skipped),
                             (["ann", "cy"], {"unsubscribed": 1}))
            campaign = admin_email.create_campaign("News", "Hi {name}", "marketing", {"ids": ids}, recipients, skipped)
        with mock.patch.object(mailer, "_deliver", side_effect=[(True, None, "<1>"), (False, "550 no", None)]) as deliver:
            sent = admin_email.run(campaign, sleep=lambda s: None)
        self.assertEqual((sent, deliver.call_count), (2, 2))
        self.assertEqual(admin_email.run(campaign, sleep=lambda s: None), 0, "a second pass sends nothing again")
        row = db.query("SELECT status, sent, failed, skipped, recipients FROM email_campaigns WHERE id = %s", (campaign,), one=True)
        self.assertEqual(dict(row), {"status": "done", "sent": 1, "failed": 1, "skipped": 1, "recipients": 2})
        logs = db.query("SELECT status FROM email_log WHERE campaign_id = %s ORDER BY id", (campaign,))
        self.assertEqual([r["status"] for r in logs], ["sent", "failed"])


if __name__ == "__main__":
    unittest.main()
