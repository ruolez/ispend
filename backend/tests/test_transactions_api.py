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
import transactions_api as tapi  # noqa: E402
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


class RangeBoundsTest(unittest.TestCase):
    def test_presets(self):
        ref = date(2026, 3, 15)
        self.assertEqual(tapi.range_bounds("this-month", ref), (date(2026, 3, 1), None))
        self.assertEqual(tapi.range_bounds("last-month", ref), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(tapi.range_bounds("last-30", ref), (date(2026, 2, 14), None))
        self.assertEqual(tapi.range_bounds("this-year", ref), (date(2026, 1, 1), None))
        self.assertEqual(tapi.range_bounds("last-year", ref), (date(2025, 1, 1), date(2025, 12, 31)))
        self.assertEqual(tapi.range_bounds("all", ref), (None, None))

    def test_last_month_across_january(self):
        self.assertEqual(tapi.range_bounds("last-month", date(2026, 1, 5)), (date(2025, 12, 1), date(2025, 12, 31)))


class BuildFiltersTest(unittest.TestCase):
    def test_all_filters(self):
        args = {"account_id": "1,2", "category_id": "5,none", "from": "2026-01-01", "to": "2026-01-31",
                "q": "star", "status": "uncategorized", "transfers": "exclude", "min": "5", "max": "50",
                "statement_id": "9"}
        sql, params = tapi.build_filters(args, 42)
        self.assertEqual(params, [42, [1, 2], [5], [5], date(2026, 1, 1), date(2026, 1, 31),
                                  "%star%", "%star%", "%star%", "%star%", Decimal("5"), Decimal("50"), 9])
        for frag in ("t.account_id = ANY", "t.category_id IS NULL", "t.txn_date >=", "ILIKE",
                     "t.category_id IS NULL AND NOT t.is_transfer", "NOT t.is_transfer", "abs(t.amount) >=",
                     "t.statement_id = %s"):
            self.assertIn(frag, sql)

    def test_range_ignored_when_explicit_dates(self):
        sql, params = tapi.build_filters({"range": "last-year", "from": "2026-02-01"}, 1)
        self.assertEqual(params, [1, date(2026, 2, 1)])
        self.assertNotIn("<=", sql)

    def test_month_preset_bounds_the_query(self):
        sql, params = tapi.build_filters({"range": "month:2026-08"}, 1)
        self.assertEqual(params, [1, date(2026, 8, 1), date(2026, 8, 31)])
        self.assertIn("t.txn_date >= %s", sql)
        self.assertIn("t.txn_date <= %s", sql)

    def test_junk_is_ignored(self):
        sql, params = tapi.build_filters({"account_id": "x", "min": "abc", "statement_id": "nope"}, 1)
        self.assertEqual((sql, params), (" t.user_id = %s", [1]))


class CursorTest(unittest.TestCase):
    def test_round_trip_date_and_amount(self):
        row = {"id": 17, "txn_date": date(2026, 9, 2), "amount": Decimal("-6.45"), "merchant_name": "Starbucks"}
        c = tapi.encode_cursor(row, "-date")
        self.assertEqual(tapi.decode_cursor(c, "-date"), (date(2026, 9, 2), 17))
        c2 = tapi.encode_cursor(row, "amount")
        self.assertEqual(tapi.decode_cursor(c2, "amount"), (Decimal("-6.45"), 17))
        c3 = tapi.encode_cursor(row, "merchant")
        self.assertEqual(tapi.decode_cursor(c3, "merchant"), ("Starbucks", 17))

    def test_clause_direction(self):
        row = {"id": 17, "txn_date": date(2026, 9, 2), "amount": Decimal("1"), "merchant_name": "M"}
        sql, params = tapi.cursor_clause("-date", tapi.encode_cursor(row, "-date"))
        self.assertEqual((sql, params), (" AND (t.txn_date, t.id) < (%s, %s)", [date(2026, 9, 2), 17]))
        sql, _ = tapi.cursor_clause("date", tapi.encode_cursor(row, "date"))
        self.assertIn(") > (", sql)
        self.assertEqual(tapi.cursor_clause("-date", "garbage!!"), ("", []))
        self.assertEqual(tapi.cursor_clause("-date", None), ("", []))


class ListEndpointTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(tapi.bp)
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s["user_id"] = 1
            s["username"] = "u"
            s["role"] = "user"

    def test_list_paginates_and_returns_facets(self):
        items = [{"id": i, "txn_date": date(2026, 9, 1), "amount": Decimal("-1.5"), "merchant_name": "M",
                  "posted_date": None} for i in range(5, 2, -1)]

        def handler(sql, params, one):
            if "COUNT(*) AS total" in sql:
                return {"total": 3, "sum_in": Decimal("0"), "sum_out": Decimal("-4.5"), "uncategorized": 1, "suggested": 0, "transfer": 0}
            if "t.account_id AS id" in sql:
                return [{"id": 1, "n": 3}]
            if "t.category_id AS id" in sql:
                return [{"id": None, "n": 1}, {"id": 5, "n": 2}]
            if "DISTINCT t.currency" in sql:
                return [{"currency": "USD"}]
            self.assertIn("LIMIT %s", sql)
            self.assertEqual(params[-1], 3)
            return items
        with patch_db(FakeDB(handler)):
            res = self.client.get("/api/transactions?limit=2&sort=-date")
        body = json.loads(res.get_data())
        self.assertEqual(res.status_code, 200)
        self.assertEqual([i["id"] for i in body["items"]], [5, 4])
        self.assertEqual(body["items"][0]["amount"], -1.5)
        self.assertIsNotNone(body["next_cursor"])
        self.assertEqual(tapi.decode_cursor(body["next_cursor"], "-date"), (date(2026, 9, 1), 4))
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["sum_out"], -4.5)
        self.assertEqual(body["facets"]["categories"], [{"id": None, "n": 1}, {"id": 5, "n": 2}])
        self.assertEqual(body["facets"]["status"]["uncategorized"], 1)

    def test_list_scopes_by_user(self):
        seen = []

        def handler(sql, params, one):
            seen.append(params)
            return {} if one else []
        with patch_db(FakeDB(handler)):
            self.client.get("/api/transactions")
        self.assertTrue(all(p[0] == 1 for p in seen))

    def test_requires_login(self):
        with self.client.session_transaction() as s:
            s.clear()
        self.assertEqual(self.client.get("/api/transactions").status_code, 401)

    def test_bulk_rejects_unknown_action_and_missing_ids(self):
        with patch_db(FakeDB(lambda sql, params, one: [{"id": 1}])):
            res = self.client.post("/api/transactions/bulk", json={"ids": [1], "action": "explode"})
            self.assertEqual(res.status_code, 400)
            res = self.client.post("/api/transactions/bulk", json={"ids": [], "action": "delete"})
            self.assertEqual(res.status_code, 400)

    def test_bulk_exclude(self):
        fake = FakeDB(lambda sql, params, one: [{"id": 1}, {"id": 2}])
        with patch_db(fake):
            res = self.client.post("/api/transactions/bulk", json={"ids": [1, 2, 99], "action": "exclude"})
        self.assertEqual(res.status_code, 200)
        upd = [c for c in fake.calls if c[0] == "execute" and "is_excluded = %s" in c[1]][0]
        self.assertEqual(upd[2], (True, [1, 2]))

    def test_rule_draft_uses_merchant_key(self):
        row = {"id": 1, "merchant_name": "Starbucks", "merchant_key": "STARBUCKS", "description_clean": "STARBUCKS 12",
               "category_id": 5, "transfer_pair_id": None, "statement_id": None}
        with patch_db(FakeDB(lambda sql, params, one: row)):
            res = self.client.post("/api/transactions/1/rule-draft")
        body = json.loads(res.get_data())
        self.assertEqual((body["match_type"], body["match_field"], body["pattern"], body["category_id"]),
                         ("equals", "merchant_key", "STARBUCKS", 5))


if __name__ == "__main__":
    unittest.main()


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "t"
        self.app.register_blueprint(tapi.bp)

    def _call(self, q, handler):
        fake = FakeDB(handler)
        with patch_db(fake), self.app.test_request_context(f"/api/transactions/suggest?q={q}"):
            from flask import session
            session["user_id"] = 1
            resp = tapi.suggest()
            body = resp[0] if isinstance(resp, tuple) else resp
            return json.loads(body.get_data()), fake

    def test_short_query_returns_nothing_without_touching_db(self):
        out, fake = self._call("s", lambda sql, params, one: self.fail("db must not be queried"))
        self.assertEqual((out, fake.calls), ([], []))

    def test_query_scopes_by_user_and_prefers_memory_category(self):
        rows = [{"merchant_key": "STARBUCKS", "merchant_name": "Starbucks", "category_id": 12, "n": 3}]
        out, fake = self._call("star", lambda sql, params, one: rows)
        sql, params = fake.calls[0][1], fake.calls[0][2]
        self.assertIn("COALESCE(m.category_id, h.recent_category_id)", sql)
        self.assertEqual((params[0], params[-2:]), (1, ("star%", 8)))
        self.assertEqual(out, rows)


class MonthRangeBoundsTest(unittest.TestCase):
    def test_month_preset_gives_first_and_last_day(self):
        self.assertEqual(tapi.range_bounds("month:2026-02"), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(tapi.range_bounds("month:2026-12"), (date(2026, 12, 1), date(2026, 12, 31)))
        self.assertEqual(tapi.range_bounds("month:bogus"), (None, None))
