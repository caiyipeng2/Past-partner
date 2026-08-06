import os
import unittest
from unittest.mock import patch

from src.server.config import ServerConfig


class ServerConfigTests(unittest.TestCase):
    def test_import_limit_is_configurable_from_environment(self) -> None:
        with patch.dict(os.environ, {"PAST_PARTNER_MAX_IMPORT_BYTES": "987654321"}, clear=False):
            config = ServerConfig.from_env()

        self.assertEqual(987654321, config.max_import_bytes)

    def test_raw_retention_is_disabled_by_default_and_configurable(self) -> None:
        with patch.dict(os.environ, {"PAST_PARTNER_RAW_RETENTION_SECONDS": "86400"}, clear=False):
            config = ServerConfig.from_env()

        self.assertEqual(86400, config.raw_retention_seconds)
        self.assertEqual(0, ServerConfig().raw_retention_seconds)

    def test_raw_retention_rejects_negative_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "retention"):
            ServerConfig(raw_retention_seconds=-1).validated()


if __name__ == "__main__":
    unittest.main()
