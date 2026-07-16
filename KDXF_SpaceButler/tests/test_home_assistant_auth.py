from __future__ import annotations

from io import BytesIO
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from spacebutler.home_assistant import HomeAssistantClient


class HomeAssistantAuthenticationTests(unittest.TestCase):
    def test_unauthorized_request_refreshes_token_and_retries_once(self) -> None:
        requests = []
        refresh_calls = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                raise HTTPError(
                    request.full_url,
                    401,
                    "Unauthorized",
                    {},
                    BytesIO(b"401: Unauthorized"),
                )
            return BytesIO(b'{"message":"API running."}')

        def refresh_token() -> str:
            refresh_calls.append(True)
            return "new-token"

        client = HomeAssistantClient(
            "http://home-assistant.local",
            "old-token",
            token_refresher=refresh_token,
        )

        with patch("spacebutler.home_assistant.urlopen", side_effect=fake_urlopen):
            result = client._request("GET", "/api/")

        self.assertEqual(result, {"message": "API running."})
        self.assertEqual(len(refresh_calls), 1)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer old-token")
        self.assertEqual(requests[1].get_header("Authorization"), "Bearer new-token")


if __name__ == "__main__":
    unittest.main()
