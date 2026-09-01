"""OpenAI-compatible client coverage for local llama.cpp and DeepSeek."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from spacebutler.llm.client import LlmClient, _preferred_endpoint


class _Response:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._body


class EndpointTests(unittest.TestCase):
    def test_deepseek_official_base_uses_documented_unversioned_path(self) -> None:
        self.assertEqual(
            _preferred_endpoint("https://api.deepseek.com", "chat/completions"),
            "https://api.deepseek.com/chat/completions",
        )

    def test_local_llama_cpp_keeps_v1_path(self) -> None:
        self.assertEqual(
            _preferred_endpoint("http://127.0.0.1:12881", "chat/completions"),
            "http://127.0.0.1:12881/v1/chat/completions",
        )

    @patch("spacebutler.llm.client.urlopen")
    def test_remote_probe_sends_bearer_auth(self, mocked_urlopen) -> None:
        health_error = HTTPError("https://api.deepseek.com/health", 404, "not found", {}, io.BytesIO())
        mocked_urlopen.side_effect = [health_error, _Response({"data": []})]
        client = LlmClient("https://api.deepseek.com", model="deepseek-v4-flash", api_key="test-key")

        self.assertTrue(client.available(force_refresh=True))
        models_request = mocked_urlopen.call_args_list[1].args[0]
        self.assertEqual(models_request.full_url, "https://api.deepseek.com/models")
        self.assertEqual(models_request.get_header("Authorization"), "Bearer test-key")

    @patch("spacebutler.llm.client.urlopen")
    def test_deepseek_structured_call_uses_json_object_mode(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = _Response({"choices": [{"message": {"content": '{"ok": true}'}}]})
        client = LlmClient("https://api.deepseek.com", model="deepseek-v4-flash", api_key="test-key")

        content = client._completion(
            "Return JSON.",
            "hello",
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        )

        self.assertEqual(content, '{"ok": true}')
        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(payload["model"], "deepseek-v4-flash")
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")


if __name__ == "__main__":
    unittest.main()
