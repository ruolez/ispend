import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import _stubs  # noqa: E402

FAKE = _stubs.install()

from flask import Flask  # noqa: E402

import tags_api  # noqa: E402
import util  # noqa: E402

TAG = {"id": 5, "name": "Trip", "color": "c4", "sort_order": 0, "created_at": None, "txn_count": 3}


class TagsApiTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(tags_api.bp)
        self.q = _stubs.Router()
        self.x = _stubs.Router(default=1)

    def _call(self, method, path, body=None):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["user_id"] = 1
        with mock.patch.object(FAKE, "query", side_effect=self.q), mock.patch.object(FAKE, "execute", side_effect=self.x), \
                mock.patch.object(util, "db", FAKE), mock.patch.object(tags_api, "db", FAKE):
            return getattr(c, method)(path, data=json.dumps(body) if body is not None else None, content_type="application/json")

    def test_create_validates_and_picks_a_colour(self):
        for body, msg in ({}, "Name is required"), ({"name": "x" * 41}, "Name must be at most 40 characters"), ({"name": "Trip", "color": "red"}, "Invalid color slot"):
            res = self._call("post", "/api/tags", body)
            self.assertEqual((res.status_code, res.get_json()["error"]), (400, msg), body)
        self.q.routes += [("lower(name) = lower(%s)", None), ("GROUP BY color", [{"color": "c1", "n": 2}]), ("FROM tags g WHERE g.id", TAG)]
        self.x.routes += [("INSERT INTO tags", {"id": 5})]
        res = self._call("post", "/api/tags", {"name": "  Trip   2026 "})
        self.assertEqual(res.status_code, 201)
        self.assertEqual(self.x.sql("INSERT INTO tags")[0][1], (1, "Trip 2026", "c2"))

    def test_duplicate_name_is_case_insensitive(self):
        self.q.routes += [("lower(name) = lower(%s)", {"id": 9})]
        res = self._call("post", "/api/tags", {"name": "trip"})
        self.assertEqual((res.status_code, res.get_json()["error"]), (400, "A tag with that name already exists"))

    def test_update_and_delete_require_ownership(self):
        self.assertEqual(self._call("put", "/api/tags/5", {"name": "x"}).status_code, 404)
        self.assertEqual(self._call("delete", "/api/tags/5").status_code, 404)
        self.q.routes += [("FROM tags g WHERE g.id", TAG), ("lower(name) = lower(%s)", None)]
        res = self._call("put", "/api/tags/5", {"name": "Holiday", "color": "c7"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.x.sql("UPDATE tags")[0][1], ("Holiday", "c7", 5, 1))
        res = self._call("delete", "/api/tags/5")
        self.assertEqual(res.get_json(), {"ok": True, "removed_from": 3})
        self.assertEqual(self.x.sql("DELETE FROM tags")[0][1], (5, 1))

    def test_list_is_user_scoped_with_counts(self):
        self.q.routes += [("FROM tags g LEFT JOIN", [TAG])]
        res = self._call("get", "/api/tags")
        self.assertEqual(res.get_json(), [{"id": 5, "name": "Trip", "color": "c4", "sort_order": 0, "txn_count": 3, "created_at": None}])
        self.assertEqual(self.q.sql("FROM tags g LEFT JOIN")[0][1], (1,))


if __name__ == "__main__":
    unittest.main()
