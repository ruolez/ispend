import os
import sys
import types
import unittest
from decimal import Decimal
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
sys.modules.setdefault("db", types.SimpleNamespace(
    get_setting=lambda *a, **k: None, set_setting=lambda *a, **k: None,
    query=lambda *a, **k: None, execute=lambda *a, **k: None, execute_values=lambda *a, **k: None))
sys.modules.setdefault("config", types.SimpleNamespace(
    STATEMENTS_DIR="/tmp", MAX_UPLOAD_BYTES=1 << 20, OCR_TIMEOUT_SECONDS=10, OCR_LANGS="eng",
    OPENROUTER_BASE_URL="http://localhost", OPENROUTER_TIMEOUT=5, AI_BATCH_SIZE=40, APP_TIMEZONE="UTC"))

import openrouter  # noqa: E402
import transfers  # noqa: E402


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class PerUserOpenRouterSettingsTest(unittest.TestCase):
    """Settings resolve user -> shared (admin) -> empty, and the enabled flag is per user."""

    def setUp(self):
        self.store = {}
        self._patch = mock.patch.object(openrouter.db, "get_setting", side_effect=lambda k, d=None: self.store.get(k, d),
                                        create=True)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_user_key_wins_over_shared_key(self):
        self.store.update({"openrouter_api_key": "shared-key", "openrouter_model": "shared/model",
                           "u7:openrouter_api_key": "mine"})
        self.assertEqual((openrouter.api_key(7), openrouter.model(7)), ("mine", "shared/model"))
        self.assertEqual(openrouter.api_key(8), "shared-key")

    def test_enabled_requires_key_model_and_flag(self):
        self.store.update({"u7:openrouter_api_key": "k", "u7:openrouter_model": "m"})
        self.assertFalse(openrouter.enabled("categorize", 7))
        self.store["u7:ai_categorize_enabled"] = "1"
        self.assertTrue(openrouter.enabled("categorize", 7))
        self.assertFalse(openrouter.enabled("categorize", 8))

    def test_http_status_maps_to_clear_message(self):
        cases = {401: "Invalid API key", 402: "Insufficient credits", 429: "Rate limited"}
        for status, text in cases.items():
            with self.subTest(status=status):
                with mock.patch("requests.post", return_value=FakeResponse(status, {"error": {"message": "x"}})):
                    with self.assertRaises(openrouter.OpenRouterError) as ctx:
                        openrouter.chat_json("s", "u", key="k", model_id="m")
                self.assertIn(text, str(ctx.exception))


class TransferPairOrderTest(unittest.TestCase):
    def test_pair_reports_ids_in_requested_order(self):
        rows = [
            {"id": 2, "account_id": 20, "amount": Decimal("100"), "transfer_pair_id": None, "account_type": "checking"},
            {"id": 1, "account_id": 10, "amount": Decimal("-100"), "transfer_pair_id": None, "account_type": "credit_card"},
        ]
        with mock.patch.object(transfers.db, "query", side_effect=[rows, {"id": 55}], create=True), \
                mock.patch.object(transfers.db, "execute", create=True), \
                mock.patch.object(transfers.db, "transaction", create=True) as tx, \
                mock.patch.object(transfers, "record_events", create=True):
            tx.return_value.__enter__ = lambda *a: None
            tx.return_value.__exit__ = lambda *a: False
            out = transfers.pair(1, 1, 2)
        self.assertEqual((out["a_id"], out["b_id"]), (1, 2))


if __name__ == "__main__":
    unittest.main()
