"""Every response carries Server-Timing (app and database milliseconds), so slow endpoints show up in
the browser's network panel and in the probe without any extra logging."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()


class ServerTimingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db as fake
        with mock.patch.object(fake, "query", return_value=None), mock.patch.object(fake, "execute", return_value=None):
            import app
            cls.app = app.create_app()

    def test_responses_report_app_and_db_time(self):
        res = self.app.test_client().get("/api/health")
        self.assertRegex(res.headers.get("Server-Timing", ""), r"^db;dur=\d+(\.\d)?, app;dur=\d+(\.\d)?$")


if __name__ == "__main__":
    unittest.main()
