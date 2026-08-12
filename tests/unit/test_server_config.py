import os
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.support.tls_fixtures import create_server_certificate
from src.server.config import ServerConfig


def _token() -> str:
    return base64.b64encode(b"d" * 32).decode("ascii")


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

    def test_device_pairing_accepts_private_host_matching_tls_certificate(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd() / ".test-runtime") as directory:
            certificate, key, _ = create_server_certificate(Path(directory), "192.168.50.7")
            config = ServerConfig(
                host="192.168.50.7",
                mode="development",
                owner_bootstrap_token=base64.b64encode(b"o" * 32).decode("ascii"),
                device_bootstrap_token=_token(),
                device_allowed_networks=("192.168.50.42/32",),
                device_tls_cert_file=certificate,
                device_tls_key_file=key,
            ).validated()
            settings = config.device_pairing_settings

        self.assertTrue(config.device_pairing_enabled)
        self.assertIsNotNone(settings)
        self.assertEqual("192.168.50.7", str(settings.host))
        self.assertEqual(32, len(settings.token_bytes))

    def test_device_pairing_rejects_public_host_and_equal_tokens(self) -> None:
        with self.assertRaisesRegex(ValueError, "private"):
            ServerConfig(
                host="8.8.8.8",
                mode="development",
                device_bootstrap_token=_token(),
                device_allowed_networks=("192.168.50.42/32",),
                device_tls_cert_file=Path("cert.pem"),
                device_tls_key_file=Path("key.pem"),
            ).validated()

        with tempfile.TemporaryDirectory(dir=Path.cwd() / ".test-runtime") as directory:
            certificate, key, _ = create_server_certificate(Path(directory), "192.168.50.7")
            same = _token()
            with self.assertRaisesRegex(ValueError, "differ"):
                ServerConfig(
                    host="192.168.50.7",
                    mode="development",
                    owner_bootstrap_token=same,
                    device_bootstrap_token=same,
                    device_allowed_networks=("192.168.50.42/32",),
                    device_tls_cert_file=certificate,
                    device_tls_key_file=key,
                ).validated()

    def test_device_pairing_rejects_broad_network_and_short_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "allowed network"):
            ServerConfig(
                host="192.168.50.7",
                mode="development",
                device_bootstrap_token=_token(),
                device_allowed_networks=("192.168.0.0/16",),
                device_tls_cert_file=Path("cert.pem"),
                device_tls_key_file=Path("key.pem"),
            ).validated()

        with tempfile.TemporaryDirectory(dir=Path.cwd() / ".test-runtime") as directory:
            certificate, key, _ = create_server_certificate(Path(directory), "192.168.50.7")
            with self.assertRaisesRegex(ValueError, "32 bytes"):
                ServerConfig(
                    host="192.168.50.7",
                    mode="development",
                    device_bootstrap_token=base64.b64encode(b"short").decode("ascii"),
                    device_allowed_networks=("192.168.50.42/32",),
                    device_tls_cert_file=certificate,
                    device_tls_key_file=key,
                ).validated()

    def test_device_pairing_accepts_ula_ipv6_and_rejects_mapped_or_zone_hosts(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd() / ".test-runtime") as directory:
            certificate, key, _ = create_server_certificate(Path(directory), "fd12:3456:789a::7")
            config = ServerConfig(
                host="fd12:3456:789a::7",
                mode="development",
                device_bootstrap_token=_token(),
                device_allowed_networks=("fd12:3456:789a::42/128",),
                device_tls_cert_file=certificate,
                device_tls_key_file=key,
            ).validated()
            self.assertEqual("fd12:3456:789a::7", str(config.device_pairing_settings.host))

        for host in ("::ffff:192.168.50.7", "fe80::1%12", "localhost", "0.0.0.0"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "private"):
                ServerConfig(
                    host=host,
                    mode="development",
                    device_bootstrap_token=_token(),
                    device_allowed_networks=("192.168.50.42/32",),
                    device_tls_cert_file=Path("cert.pem"),
                    device_tls_key_file=Path("key.pem"),
                ).validated()

    def test_device_pairing_from_env_parses_all_values_as_one_group(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd() / ".test-runtime") as directory:
            certificate, key, _ = create_server_certificate(Path(directory), "192.168.50.7")
            values = {
                "PAST_PARTNER_HOST": "192.168.50.7",
                "PAST_PARTNER_MODE": "development",
                "PAST_PARTNER_DEV_DEVICE_BOOTSTRAP_TOKEN": _token(),
                "PAST_PARTNER_DEV_DEVICE_ALLOWED_NETWORKS": "192.168.50.42/32",
                "PAST_PARTNER_DEV_DEVICE_TLS_CERT_FILE": str(certificate),
                "PAST_PARTNER_DEV_DEVICE_TLS_KEY_FILE": str(key),
            }
            with patch.dict(os.environ, values, clear=False):
                config = ServerConfig.from_env()
        self.assertTrue(config.device_pairing_enabled)

    def test_device_pairing_errors_do_not_echo_token_or_path(self) -> None:
        secret = _token()
        path = "C:/private/device-key.pem"
        with self.assertRaises(ValueError) as captured:
            ServerConfig(
                host="192.168.50.7",
                mode="development",
                device_bootstrap_token=secret,
                device_allowed_networks=("192.168.0.0/16",),
                device_tls_cert_file=Path(path),
                device_tls_key_file=Path(path),
            ).validated()
        self.assertNotIn(secret, str(captured.exception))
        self.assertNotIn(path, str(captured.exception))


if __name__ == "__main__":
    unittest.main()
