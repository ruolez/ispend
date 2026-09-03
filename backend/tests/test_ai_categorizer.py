import os
import sys
import types
import unittest
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")


@contextmanager
def _tx():
    yield None


sys.modules.setdefault("db", types.SimpleNamespace(
    query=lambda *a, **k: None, execute=lambda *a, **k: 0, execute_values=lambda *a, **k: None, transaction=_tx,
    get_setting=lambda *a, **k: None, set_setting=lambda *a, **k: None, get_db=lambda: None,
    close_db=lambda *a, **k: None))
DB = sys.modules["db"]

import ai_categorizer  # noqa: E402
import openrouter  # noqa: E402

ROWS = [
    {"id": 11, "description_clean": "STARBUCKS", "merchant_name": "Starbucks", "amount": Decimal("-6.45"), "txn_date": date(2026, 9, 1)},
    {"id": 12, "description_clean": "SHELL OIL", "merchant_name": "Shell", "amount": Decimal("-52.10"), "txn_date": date(2026, 9, 2)},
]
CATS = [{"id": 3, "name": "Coffee", "kind": "expense", "parent_name": "Dining"},
        {"id": 5, "name": "Fuel", "kind": "expense", "parent_name": "Transport"}]


def fake_query(sql, params=None, one=False, **kw):
    if "FROM categories" in sql:
        return CATS
    if "FROM transactions" in sql:
        return ROWS
    return [] if not one else None


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self.executed = []
        self.events = []
        self.p = [
            mock.patch.object(DB, "query", side_effect=fake_query, create=True),
            mock.patch.object(DB, "execute", side_effect=lambda sql, params=None, **k: self.executed.append(params) or 1, create=True),
            mock.patch.object(ai_categorizer, "record_events", side_effect=lambda ev, **k: self.events.extend(ev)),
            mock.patch.object(openrouter, "enabled", return_value=True),
            mock.patch.object(openrouter, "model", return_value="test/model"),
            mock.patch.object(openrouter, "log_call"),
        ]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_disabled_is_a_silent_noop(self):
        with mock.patch.object(openrouter, "enabled", return_value=False), mock.patch.object(openrouter, "chat_json") as chat:
            self.assertEqual(ai_categorizer.suggest_for_ids(1, [11, 12]), {"suggested": 0, "skipped": "disabled"})
        chat.assert_not_called()

    def test_applies_valid_suggestions_only(self):
        reply = {"items": [
            {"i": 0, "category_id": 3, "confidence": 0.93, "reason": "coffee shop"},
            {"i": 1, "category_id": 999, "confidence": 0.9},          # unknown category -> dropped
            {"i": 7, "category_id": 5, "confidence": 0.9},            # bad index -> dropped
        ]}
        with mock.patch.object(openrouter, "chat_json", return_value=(reply, {"prompt_tokens": 1, "completion_tokens": 1})) as chat:
            result = ai_categorizer.suggest_for_ids(1, [11, 12])
        self.assertEqual(result, {"suggested": 1, "batches": 1, "errors": 0})
        self.assertEqual(self.executed, [(3, 0.93, "coffee shop", 11, 1)])
        self.assertEqual(self.events, [(11, "ai", {"category_id": 3, "confidence": 0.93, "reason": "coffee shop"}, None)])
        prompt = chat.call_args.args[1]
        self.assertIn("3: Dining > Coffee", prompt)
        self.assertIn('"merchant": "Starbucks"', prompt)

    def test_confidence_is_clamped(self):
        reply = {"items": [{"i": 1, "category_id": 5, "confidence": 7}]}
        with mock.patch.object(openrouter, "chat_json", return_value=(reply, {})):
            out = ai_categorizer.suggest_sync(1, [12])
        self.assertEqual(out, {"suggested": 1, "items": [{"id": 12, "category_id": 5, "confidence": 1.0, "reason": ""}]})

    def test_batch_error_is_swallowed_in_background(self):
        with mock.patch.object(openrouter, "chat_json", side_effect=openrouter.OpenRouterError("boom")):
            self.assertEqual(ai_categorizer.suggest_for_ids(1, [11, 12]), {"suggested": 0, "batches": 1, "errors": 1})

    def test_sync_raises_when_disabled(self):
        with mock.patch.object(openrouter, "enabled", return_value=False):
            with self.assertRaises(openrouter.OpenRouterError):
                ai_categorizer.suggest_sync(1, [11])


if __name__ == "__main__":
    unittest.main()
