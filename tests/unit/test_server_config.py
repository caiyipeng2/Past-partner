import os
import unittest
from pathlib import Path
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

    def test_env_template_exposes_disabled_raw_retention_setting(self) -> None:
        template = Path(__file__).parents[2] / ".env.example"

        self.assertIn("PAST_PARTNER_RAW_RETENTION_SECONDS=0", template.read_text(encoding="utf-8"))

    def test_model_pricing_json_is_loaded_from_environment(self) -> None:
        raw = '{"deepseek/deepseek-v4-flash":{"input_price_per_million_tokens":0.14}}'
        with patch.dict(os.environ, {"PAST_PARTNER_MODEL_PRICING_JSON": raw}, clear=False):
            config = ServerConfig.from_env()

        self.assertEqual(raw, config.model_pricing_json)
        self.assertIsNone(ServerConfig().model_pricing_json)


if __name__ == "__main__":
    unittest.main()
