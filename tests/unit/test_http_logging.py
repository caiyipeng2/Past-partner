import io
import logging
import unittest
from unittest.mock import patch

from src.server.http import ApiRequestHandler


class HttpLoggingUnitTests(unittest.TestCase):
    def test_handler_log_message_is_disabled(self) -> None:
        handler = object.__new__(ApiRequestHandler)
        with patch("src.server.http.logger") as logger:
            handler.log_message("secret %s", "value")
        logger.info.assert_not_called()


if __name__ == "__main__":
    unittest.main()
