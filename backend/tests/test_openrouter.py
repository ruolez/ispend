import os
import sys
import types
import unittest
from unittest import mock

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
sys.modules.setdefault("db", types.SimpleNamespace(
    query=lambda *a, **k: None, execute=lambda *a, **k: 0, execute_values=lambda *a, **k: None,
    get_setting=lambda *a, **k: None, set_setting=lambda *a, **k: None, get_db=lambda: None,
    close_db=lambda *a, **k: None))

import openrouter  # noqa: E402


class FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def completion(content):
    return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class ChatJsonTest(unittest.TestCase):
    def test_parses_fenced_json(self):
        with mock.patch("requests.post", return_value=FakeResponse(200, completion('```json\n{"ok": true}\n```'))) as post:
            parsed, usage = openrouter.chat_json("sys", "user", model_id="m", key="k")
        self.assertEqual((parsed, usage), ({"ok": True}, {"prompt_tokens": 10, "completion_tokens": 5}))
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer k")

    def test_extracts_object_from_prose(self):
        with mock.patch("requests.post", return_value=FakeResponse(200, completion('Sure! {"items": [1]} done'))):
            parsed, _ = openrouter.chat_json("s", "u", model_id="m", key="k")
        self.assertEqual(parsed, {"items": [1]})

    def test_error_status_maps_to_exception(self):
        payload = {"error": {"code": 401, "message": "Invalid key"}}
        with mock.patch("requests.post", return_value=FakeResponse(401, payload)):
            with self.assertRaises(openrouter.OpenRouterError) as ctx:
                openrouter.chat_json("s", "u", model_id="m", key="k")
        self.assertIn("Invalid key", str(ctx.exception))

    def test_non_json_content_raises(self):
        with mock.patch("requests.post", return_value=FakeResponse(200, completion("no json here"))):
            with self.assertRaises(openrouter.OpenRouterError):
                openrouter.chat_json("s", "u", model_id="m", key="k")

    def test_missing_configuration_raises_without_network(self):
        with mock.patch("requests.post") as post, mock.patch.object(openrouter, "api_key", return_value=""):
            with self.assertRaises(openrouter.OpenRouterError):
                openrouter.chat_json("s", "u", model_id="m")
        post.assert_not_called()


class ListModelsTest(unittest.TestCase):
    def test_normalizes_catalog(self):
        payload = {"data": [
            {"id": "z/model", "name": "Z", "context_length": 8000,
             "pricing": {"prompt": "0.000001", "completion": "0.000002"}, "supported_parameters": ["response_format"]},
            {"id": "a/model", "name": "A", "context_length": 4000, "pricing": {}, "supported_parameters": []},
        ]}
        with mock.patch("requests.get", return_value=FakeResponse(200, payload)):
            models = openrouter.list_models(force=True)
        self.assertEqual([m["id"] for m in models], ["a/model", "z/model"])
        self.assertEqual(models[1]["prompt_price"], 1.0)
        self.assertEqual(models[1]["completion_price"], 2.0)
        self.assertEqual([m["structured"] for m in models], [False, True])


if __name__ == "__main__":
    unittest.main()
