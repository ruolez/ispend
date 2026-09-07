import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import ai_api  # noqa: E402
import openrouter  # noqa: E402
import util  # noqa: E402


class AiApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(ai_api.bp)
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(ai_api, "db", FAKE), mock.patch.object(openrouter, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None, content_type="application/json")

    def test_categorize_without_ai_is_502_not_500(self):
        FAKE.settings.clear()
        res = self._call("post", "/api/ai/categorize", {"scope": "uncategorized"})
        self.assertEqual(res.status_code, 502)
        self.assertIn("not enabled", res.get_json()["error"])

    def test_non_object_body_is_400(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
        res = c.post("/api/ai/categorize", data=json.dumps([1]), content_type="application/json")
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
