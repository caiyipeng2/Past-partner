import io
import logging
import unittest
from unittest.mock import patch

from src.server.http import ApiRequestHandler, _route_template


class HttpLoggingUnitTests(unittest.TestCase):
    def test_handler_log_message_is_disabled(self) -> None:
        handler = object.__new__(ApiRequestHandler)
        with patch("src.server.http.logger") as logger:
            handler.log_message("secret %s", "value")
        logger.info.assert_not_called()

    def test_learning_routes_use_parameterized_templates(self) -> None:
        self.assertEqual(
            "/api/v1/personas/{persona_id}/learning/style-profile",
            _route_template("/api/v1/personas/persona-1/learning/style-profile?token=secret"),
        )
        self.assertEqual(
            "/api/v1/personas/{persona_id}/learning/memory/{memory_id}",
            _route_template(f"/api/v1/personas/persona-1/learning/memory/{'a' * 64}"),
        )


if __name__ == "__main__":
    unittest.main()
