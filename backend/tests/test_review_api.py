import json
import os
import unittest
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")

import db  # noqa: E402
import categorizer  # noqa: E402
import review_api  # noqa: E402
from flask import Flask  # noqa: E402


class FakeDB:
    def __init__(self, handler):
        self.calls = []
        self.handler = handler

    def query(self, sql, params=None, one=False, commit=True):
        self.calls.append(("query", sql, params))
        return self.handler(sql, params, one)

    def execute(self, sql, params=None, returning=False, commit=True):
        self.calls.append(("execute", sql, params))
        return self.handler(sql, params, returning) if returning else 1

    def execute_values(self, sql, rows, template=None, commit=True, page_size=500):
        self.calls.append(("execute_values", sql, rows))

    @contextmanager
    def transaction(self):
        yield None


def patch_db(fake):
    return mock.patch.multiple(db, query=fake.query, execute=fake.execute, execute_values=fake.execute_values,
                               transaction=fake.transaction, create=True)


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(review_api.bp)
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s["user_id"] = 1
            s["role"] = "user"

    def test_merchant_groups_shape(self):
        group = {"key": "STARBUCKS", "display": "Starbucks", "count": 3, "total": Decimal("-19.35"),
                 "first": date(2026, 8, 1), "last": date(2026, 9, 2), "ids": [9, 8, 7],
                 "suggestion_id": 5, "suggestion_confidence": Decimal("0.600"), "suggestion_source": "builtin",
                 "sample_description": "STARBUCKS #1"}
        nosug = {**group, "key": "ZQX", "display": "Zqx", "suggestion_id": None, "ids": [1]}

        def handler(sql, params, one):
            if "COUNT(DISTINCT t.merchant_key) AS groups" in sql:
                return {"groups": 2, "items": 4}
            return [group, nosug]
        with patch_db(FakeDB(handler)):
            res = self.client.get("/api/review?mode=merchant")
        body = json.loads(res.get_data())
        self.assertEqual(body["remaining"], 2)
        self.assertEqual(body["groups"][0]["suggestion"], {"category_id": 5, "confidence": 0.6, "source": "builtin"})
        self.assertEqual(body["groups"][0]["total"], -19.35)
        self.assertEqual(body["groups"][0]["ids"], [9, 8, 7])
        self.assertIsNone(body["groups"][1]["suggestion"])

    def test_count(self):
        with patch_db(FakeDB(lambda sql, params, one: {"uncategorized": 4, "suggested": 2, "total": 6})):
            body = json.loads(self.client.get("/api/review/count").get_data())
        self.assertEqual(body, {"uncategorized": 4, "suggested": 2, "total": 6})

    def test_resolve_requires_category_or_transfer(self):
        with patch_db(FakeDB(lambda sql, params, one: [{"id": 1}])):
            res = self.client.post("/api/review/resolve", json={"ids": [1]})
        self.assertEqual(res.status_code, 400)

    def test_resolve_categorizes_and_creates_rule(self):
        def handler(sql, params, one):
            if "FROM transactions WHERE user_id" in sql:
                return [{"id": 1}, {"id": 2}]
            if "FROM categories" in sql:
                return {"id": 5}
            if "MAX(priority)" in sql:
                return {"p": 30}
            if "INSERT INTO rules" in sql:
                return {"id": 77}
            return None
        fake = FakeDB(handler)
        with patch_db(fake), mock.patch.object(categorizer, "apply_manual", return_value=2) as apply:
            res = self.client.post("/api/review/resolve", json={
                "ids": [1, 2], "category_id": 5,
                "create_rule": {"pattern": "STARBUCKS", "match_type": "contains"},
            })
        body = json.loads(res.get_data())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body, {"updated": 2, "rule_id": 77})
        apply.assert_called_once_with(1, [1, 2], 5, learn_memory=True)
        insert = [c for c in fake.calls if "INSERT INTO rules" in c[1]][0]
        self.assertEqual(insert[2][2], 40)          # priority after the current max
        self.assertEqual(insert[2][8], 5)           # category_id
        self.assertEqual(insert[2][10], 2)          # hit_count = rows resolved
        link = [c for c in fake.calls if "category_rule_id = %s" in c[1]][0]
        self.assertEqual(link[2], (77, [1, 2]))

    def test_suggest_without_ai_is_502(self):
        import openrouter
        with patch_db(FakeDB(lambda sql, params, one: [])), mock.patch.object(openrouter, "enabled", return_value=False):
            res = self.client.post("/api/review/suggest", json={"ids": [1]})
        self.assertEqual(res.status_code, 502)


if __name__ == "__main__":
    unittest.main()
