import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from src.providers.base import AdapterError
from src.providers.transport import urllib_json_transport


class ProviderTransportTests(unittest.TestCase):
    def test_http_429_is_exposed_as_rate_limited(self) -> None:
        error = HTTPError(
            "https://provider.invalid/chat/completions",
            429,
            "rate limited",
            hdrs=None,
            fp=None,
        )

        with patch("src.providers.transport.urlopen", side_effect=error):
            with self.assertRaises(AdapterError) as captured:
                urllib_json_transport("https://provider.invalid", {}, {}, 1.0)

        self.assertEqual("provider_rate_limited", captured.exception.code)

    def test_timeout_is_exposed_as_timeout(self) -> None:
        with patch("src.providers.transport.urlopen", side_effect=TimeoutError()):
            with self.assertRaises(AdapterError) as captured:
                urllib_json_transport("https://provider.invalid", {}, {}, 1.0)

        self.assertEqual("provider_timeout", captured.exception.code)

    def test_non_json_response_remains_stable(self) -> None:
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b"not-json"

        with patch("src.providers.transport.urlopen", return_value=_Response()):
            with self.assertRaises(AdapterError) as captured:
                urllib_json_transport("https://provider.invalid", {}, {}, 1.0)

        self.assertEqual("invalid_provider_response", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
