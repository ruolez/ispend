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
import util  # noqa: E402
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


class CleanRestoreItemsTest(unittest.TestCase):
    OWN = {1, 2}
    ITEM = {"id": 1, "category_id": 5, "category_status": "confirmed", "category_source": "manual", "category_rule_id": 9,
            "category_confidence": "1", "ai_rationale": None, "is_transfer": 0, "is_excluded": "yes", "transfer_pair_id": 2}

    def test_drops_foreign_rows_and_unlinks_missing_rules_and_partners(self):
        out = util.clean_restore_items([self.ITEM, {"id": 3, "category_id": 5}, "junk"], self.OWN, {5}, set(), set())
        self.assertEqual(out, [{"id": 1, "category_id": 5, "category_status": "confirmed", "category_source": "manual",
                                "category_rule_id": None, "category_confidence": 1.0, "ai_rationale": None, "is_transfer": False,
                                "is_excluded": True, "transfer_pair_id": None}])

    def test_keeps_links_that_still_exist(self):
        out = util.clean_restore_items([self.ITEM], self.OWN, {5}, {9}, {2})
        self.assertEqual((out[0]["category_rule_id"], out[0]["transfer_pair_id"]), (9, 2))

    def test_rejections(self):
        for bad in ({**self.ITEM, "category_id": 6}, {**self.ITEM, "category_status": "weird"}, {**self.ITEM, "category_source": "guess"},
                    {**self.ITEM, "category_confidence": 2}, {**self.ITEM, "category_confidence": "x"}, {**self.ITEM, "id": "abc"}):
            with self.assertRaises(ValueError, msg=bad):
                util.clean_restore_items([bad], self.OWN, {5}, {9}, {2})
        with self.assertRaises(ValueError):
            util.clean_restore_items({"id": 1}, self.OWN, {5}, {9}, {2})

    def test_missing_fields_default_to_uncategorized(self):
        out = util.clean_restore_items([{"id": 2}], self.OWN, set(), set(), set())
        self.assertEqual(out[0], {"id": 2, "category_id": None, "category_status": "none", "category_source": None, "category_rule_id": None,
                                  "category_confidence": None, "ai_rationale": None, "is_transfer": False, "is_excluded": False,
                                  "transfer_pair_id": None})


class RangeBoundsTest(unittest.TestCase):
    def test_presets(self):
        ref = date(2026, 3, 15)
        self.assertEqual(tapi.range_bounds("this-month", ref), (date(2026, 3, 1), None))
        self.assertEqual(tapi.range_bounds("last-month", ref), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(tapi.range_bounds("last-30", ref), (date(2026, 2, 14), None))
        self.assertEqual(tapi.range_bounds("this-year", ref), (date(2026, 1, 1), None))
        self.assertEqual(tapi.range_bounds("last-year", ref), (date(2025, 1, 1), date(2025, 12, 31)))
        self.assertEqual(tapi.range_bounds("all", ref), (None, None))

    def test_week_presets(self):
        sunday = date(2026, 3, 15)
        self.assertEqual(tapi.range_bounds("this-week", sunday), (date(2026, 3, 15), date(2026, 3, 21)))
        self.assertEqual(tapi.range_bounds("this-week", sunday, week_start=1), (date(2026, 3, 9), date(2026, 3, 15)))
        self.assertEqual(tapi.range_bounds("last-week", sunday), (date(2026, 3, 8), date(2026, 3, 14)))
        self.assertEqual(tapi.week_bounds(date(2026, 1, 1), 6), (date(2025, 12, 27), date(2026, 1, 2)))

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

    def test_tag_filters(self):
        sql, params = tapi.build_filters({"tag": "3,4"}, 1)
        self.assertIn("tt.tag_id = ANY(%s)", sql)
        self.assertEqual(params, [1, [3, 4]])
        sql, params = tapi.build_filters({"tag": "3,4", "tag_mode": "all"}, 1)
        self.assertEqual(sql.count("EXISTS (SELECT 1 FROM transaction_tags"), 2)
        self.assertEqual(params, [1, 3, 4])
        sql, params = tapi.build_filters({"tag": "none"}, 1)
        self.assertIn("NOT EXISTS (SELECT 1 FROM transaction_tags", sql)
        self.assertEqual(params, [1])
        self.assertEqual(tapi.build_filters({"tag": "abc"}, 1), (" t.user_id = %s", [1]))

    def test_range_ignored_when_explicit_dates(self):
        sql, params = tapi.build_filters({"range": "last-year", "from": "2026-02-01"}, 1)
        self.assertEqual(params, [1, date(2026, 2, 1)])
        self.assertNotIn("<=", sql)

    def test_flow_filters_debits_or_credits(self):
        self.assertEqual(tapi.build_filters({"flow": "out"}, 1), (" t.user_id = %s AND t.amount < 0", [1]))
        self.assertEqual(tapi.build_filters({"flow": "in"}, 1), (" t.user_id = %s AND t.amount > 0", [1]))
        self.assertEqual(tapi.build_filters({"flow": "sideways"}, 1), (" t.user_id = %s", [1]))

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
            if "tt.tag_id AS id" in sql:
                return [{"id": 3, "n": 2}]
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
        self.assertEqual(body["facets"]["tags"], [{"id": 3, "n": 2}])
        self.assertEqual(tapi.decode_cursor(body["next_cursor"], "-date"), (date(2026, 9, 1), 4))
        self.assertEqual(body["total"], 3)
        self.assertEqual(body["sum_out"], -4.5)
        self.assertEqual(body["facets"]["categories"], [{"id": None, "n": 1}, {"id": 5, "n": 2}])
        self.assertEqual(body["facets"]["status"]["uncategorized"], 1)
        self.assertEqual(body["skipped"], {"count": 0, "sum": 0.0})

    def test_totals_leave_out_transfers_and_excluded_rows(self):
        seen = []

        def handler(sql, params, one):
            if "COUNT(*) AS total" in sql:
                seen.append(" ".join(sql.split()))
                return {"total": 0, "sum_in": 0, "sum_out": 0, "sum_skipped": Decimal("750.00"), "skipped": 2}
            return {} if one else []
        with patch_db(FakeDB(handler)):
            body = json.loads(self.client.get("/api/transactions").get_data())
        self.assertIn("CASE WHEN t.amount > 0 AND NOT t.is_transfer AND NOT t.is_excluded THEN t.amount END", seen[0])
        self.assertIn("CASE WHEN t.amount < 0 AND NOT t.is_transfer AND NOT t.is_excluded THEN t.amount END", seen[0])
        self.assertEqual(body["skipped"], {"count": 2, "sum": 750.0})

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

    def test_bulk_returns_before_snapshot_captured_before_the_change(self):
        snap_row = {"id": 1, "category_id": 5, "category_status": "confirmed", "category_source": "ai", "category_rule_id": None,
                    "category_confidence": Decimal("0.80"), "ai_rationale": "looks like coffee", "is_transfer": False,
                    "is_excluded": False, "transfer_pair_id": None}

        def handler(sql, params, one):
            return [snap_row] if "ai_rationale" in sql else [{"id": 1}]
        fake = FakeDB(handler)
        with patch_db(fake):
            body = json.loads(self.client.post("/api/transactions/bulk", json={"ids": [1], "action": "exclude"}).get_data())
        self.assertEqual(body, {"updated": 1, "before": [{**snap_row, "category_confidence": 0.8}]})
        kinds = [(c[0], "ai_rationale" in c[1], "is_excluded = %s" in c[1]) for c in fake.calls]
        self.assertLess(kinds.index(("query", True, False)), kinds.index(("execute", False, True)))

    def test_bulk_delete_has_no_before(self):
        fake = FakeDB(lambda sql, params, one: [{"id": 1}])
        with patch_db(fake):
            body = json.loads(self.client.post("/api/transactions/bulk", json={"ids": [1], "action": "delete"}).get_data())
        self.assertEqual(body, {"updated": 1})
        self.assertFalse(any("ai_rationale" in c[1] for c in fake.calls))

    def test_bulk_restore_writes_every_field_and_records_a_manual_event(self):
        def handler(sql, params, one):
            if "FROM categories" in sql:
                return [{"id": 5}]
            if "FROM rules" in sql:
                return [{"id": 9}]
            return [{"id": 1}, {"id": 2}]
        fake = FakeDB(handler)
        item = {"id": 1, "category_id": 5, "category_status": "suggested", "category_source": "ai", "category_rule_id": 9,
                "category_confidence": 0.7, "ai_rationale": "r", "is_transfer": False, "is_excluded": True, "transfer_pair_id": 2}
        with patch_db(fake):
            res = self.client.post("/api/transactions/bulk", json={"ids": [1], "action": "restore", "items": [item, {"id": 42}]})
        self.assertEqual(json.loads(res.get_data()), {"updated": 1})
        upd = [c for c in fake.calls if c[0] == "execute" and "category_confidence = %s" in c[1]]
        self.assertEqual(len(upd), 1)
        self.assertEqual(upd[0][2], (5, "suggested", "ai", 9, 0.7, "r", False, True, 2, 1, 1))
        ev = [c for c in fake.calls if c[0] == "execute_values"][0]
        self.assertEqual(ev[2][0][:2], (1, "manual"))
        self.assertIn('"restored": true', ev[2][0][2])

    def test_bulk_restore_rejects_a_foreign_category(self):
        fake = FakeDB(lambda sql, params, one: [] if "FROM categories" in sql else [{"id": 1}])
        with patch_db(fake):
            res = self.client.post("/api/transactions/bulk", json={"ids": [1], "action": "restore", "items": [{"id": 1, "category_id": 5}]})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(any(c[0] == "execute" for c in fake.calls))

    def test_bulk_tag_and_untag(self):
        def handler(sql, params, one):
            if "FROM tags WHERE user_id" in sql:
                return [{"id": 7}]
            return [{"id": 1}, {"id": 2}]
        fake = FakeDB(handler)
        with patch_db(fake):
            body = json.loads(self.client.post("/api/transactions/bulk", json={"ids": [1, 2], "action": "tag", "tag_ids": [7]}).get_data())
        self.assertEqual(body["updated"], 2)
        ins = [c for c in fake.calls if c[0] == "execute_values" and "INSERT INTO transaction_tags" in c[1]][0]
        self.assertEqual(ins[2], [(1, 7), (2, 7)])
        with patch_db(fake):
            self.client.post("/api/transactions/bulk", json={"ids": [1, 2], "action": "untag", "tag_ids": [7]})
        self.assertTrue(any("DELETE FROM transaction_tags" in c[1] and c[2] == ([1, 2], [7]) for c in fake.calls))
        fake2 = FakeDB(lambda sql, params, one: [] if "FROM tags WHERE user_id" in sql else [{"id": 1}])
        with patch_db(fake2):
            res = self.client.post("/api/transactions/bulk", json={"ids": [1], "action": "tag", "tag_ids": [99]})
        self.assertEqual(res.status_code, 404)

    def test_list_facets_include_excluded(self):
        def handler(sql, params, one):
            if "COUNT(*) AS total" in sql:
                return {"total": 4, "sum_in": 0, "sum_out": 0, "sum_skipped": 0, "skipped": 3, "uncategorized": 0, "suggested": 0,
                        "transfer": 1, "excluded": 2}
            return {} if one else []
        with patch_db(FakeDB(handler)):
            body = json.loads(self.client.get("/api/transactions").get_data())
        self.assertEqual(body["facets"]["status"], {"uncategorized": 0, "suggested": 0, "transfer": 1, "excluded": 2})

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


class CreateTransactionTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(tapi.bp)
        self.client = self.app.test_client()
        with self.client.session_transaction() as s:
            s["user_id"] = 1
            s["role"] = "user"

    def _post(self, body, category_lookup):
        def handler(sql, params, one):
            if "FROM accounts WHERE id" in sql:
                return {"id": 4, "currency": "USD"}
            if "FROM categories WHERE id" in sql:
                self.assertEqual(params, (body["category_id"], 1))
                return category_lookup
            if "INSERT INTO transactions" in sql:
                return {"id": 77}
            return None
        fake = FakeDB(handler)
        with patch_db(fake):
            res = self.client.post("/api/transactions", data=json.dumps(body), content_type="application/json")
        return res, fake

    def test_foreign_category_is_404_and_nothing_is_written(self):
        body = {"account_id": 4, "txn_date": "2026-01-05", "amount": "-4.00", "description": "X", "category_id": 55}
        res, fake = self._post(body, None)
        self.assertEqual((res.status_code, json.loads(res.get_data())), (404, {"error": "Category not found"}))
        self.assertEqual([c for c in fake.calls if c[0] == "execute"], [])

    def test_non_integer_category_is_400(self):
        body = {"account_id": 4, "txn_date": "2026-01-05", "amount": "-4.00", "description": "X", "category_id": "abc"}
        res, _fake = self._post(body, None)
        self.assertEqual(res.status_code, 400)
        self.assertIn(b"Category must be an integer", res.get_data())
