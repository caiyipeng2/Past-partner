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
    def test_storage_backend_defaults_to_local(self) -> None:
        self.assertEqual("local", ServerConfig().storage_backend)
        with patch.dict(os.environ, {}, clear=False):
            self.assertEqual("local", ServerConfig.from_env().storage_backend)

    def test_storage_backend_accepts_explicit_local_value(self) -> None:
        with patch.dict(os.environ, {"PAST_PARTNER_STORAGE_BACKEND": "local"}, clear=False):
            config = ServerConfig.from_env()

        self.assertEqual("local", config.storage_backend)

    def test_storage_backend_accepts_valid_s3_settings(self) -> None:
        config = ServerConfig(
            storage_backend="s3",
            storage_s3_endpoint="https://objects.example.test",
            storage_s3_bucket="past-partner-test",
            storage_s3_region="cn-test-1",
            storage_s3_access_key="access-key",
            storage_s3_secret_key="secret-key",
            storage_s3_path_style=False,
        ).validated()

        self.assertEqual("s3", config.storage_backend)
        self.assertEqual("past-partner-test", config.storage_s3_bucket)
        self.assertEqual("cn-test-1", config.storage_s3_region)
        self.assertFalse(config.storage_s3_path_style)

    def test_minio_alias_normalizes_and_allows_loopback_http_only_in_development(self) -> None:
        config = ServerConfig(
            mode="development",
            storage_backend="minio",
            storage_s3_endpoint="http://127.0.0.1:9000",
            storage_s3_bucket="past-partner-test",
            storage_s3_access_key="access-key",
            storage_s3_secret_key="secret-key",
        ).validated()

        self.assertEqual("s3", config.storage_backend)

    def test_s3_requires_bucket_and_paired_credentials(self) -> None:
        with self.assertRaises(ValueError) as missing_bucket:
            ServerConfig(storage_backend="s3").validated()
        self.assertEqual("storage_bucket_required", missing_bucket.exception.code)

        with self.assertRaises(ValueError) as one_sided:
            ServerConfig(
                storage_backend="s3",
                storage_s3_bucket="past-partner-test",
                storage_s3_access_key="access-key",
            ).validated()
        self.assertEqual("storage_credentials_invalid", one_sided.exception.code)

    def test_s3_production_rejects_plain_http_endpoint(self) -> None:
        with self.assertRaises(ValueError) as captured:
            ServerConfig(
                mode="production",
                storage_backend="s3",
                storage_s3_endpoint="http://objects.example.test",
                storage_s3_bucket="past-partner-test",
            ).validated()

        self.assertEqual("storage_endpoint_insecure", captured.exception.code)
        self.assertNotIn("objects.example.test", str(captured.exception))

    def test_s3_settings_are_loaded_from_environment(self) -> None:
        values = {
            "PAST_PARTNER_STORAGE_BACKEND": "minio",
            "PAST_PARTNER_STORAGE_S3_ENDPOINT": "http://127.0.0.1:9000",
            "PAST_PARTNER_STORAGE_S3_BUCKET": "past-partner-test",
            "PAST_PARTNER_STORAGE_S3_REGION": "local",
            "PAST_PARTNER_STORAGE_S3_ACCESS_KEY": "access-key",
            "PAST_PARTNER_STORAGE_S3_SECRET_KEY": "secret-key",
            "PAST_PARTNER_STORAGE_S3_PATH_STYLE": "false",
        }
        with patch.dict(os.environ, values, clear=False):
            config = ServerConfig.from_env()

        self.assertEqual("s3", config.storage_backend)
        self.assertEqual("local", config.storage_s3_region)
        self.assertFalse(config.storage_s3_path_style)

    def test_storage_backend_rejects_unknown_values_without_silent_fallback(self) -> None:
        for value in ("postgres", "", "C:/private/storage"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"PAST_PARTNER_STORAGE_BACKEND": value},
                clear=False,
            ):
                with self.assertRaises(ValueError) as captured:
                    ServerConfig.from_env()
                self.assertEqual("storage_backend_unsupported", captured.exception.code)

    def test_storage_backend_error_does_not_echo_secret_or_full_path(self) -> None:
        secret = "provider-secret"
        path = "C:/private/runtime/storage"
        with self.assertRaises(ValueError) as captured:
            ServerConfig(storage_backend=f"s3://{secret}/{path}").validated()

        self.assertEqual("storage_backend_unsupported", captured.exception.code)
        self.assertNotIn(secret, str(captured.exception))
        self.assertNotIn(path, str(captured.exception))

    def test_kms_source_requires_key_id_and_defaults_ciphertext_path(self) -> None:
        with self.assertRaises(ValueError) as missing:
            ServerConfig(master_key_source="kms").validated()
        self.assertEqual("master_key_kms_key_id_required", missing.exception.code)

        environment_config = ServerConfig(master_key_source="environment").validated()
        self.assertIsNone(environment_config.master_key_kms_ciphertext_file)

        config = ServerConfig(
            data_dir=Path("runtime-kms"),
            mode="production",
            master_key_source="kms",
            master_key_kms_key_id="alias/past-partner",
        ).validated()
        self.assertEqual("kms", config.master_key_source)
        self.assertEqual(
            config.data_dir / "secrets" / "master-key.kms",
            config.master_key_kms_ciphertext_file,
        )

    def test_kms_source_rejects_unknown_values_and_plain_http_in_production(self) -> None:
        with self.assertRaises(ValueError) as source:
            ServerConfig(master_key_source="vault").validated()
        self.assertEqual("master_key_source_unsupported", source.exception.code)

        with self.assertRaises(ValueError) as endpoint:
            ServerConfig(
                mode="production",
                master_key_source="kms",
                master_key_kms_key_id="alias/past-partner",
                master_key_kms_endpoint="http://kms.example.test",
            ).validated()
        self.assertEqual("master_key_kms_endpoint_insecure", endpoint.exception.code)
        self.assertNotIn("kms.example.test", str(endpoint.exception))

    def test_kms_source_is_loaded_from_environment(self) -> None:
        values = {
            "PAST_PARTNER_MASTER_KEY_SOURCE": "kms",
            "PAST_PARTNER_MASTER_KEY_KMS_KEY_ID": "alias/past-partner",
            "PAST_PARTNER_MASTER_KEY_KMS_REGION": "cn-test-1",
            "PAST_PARTNER_MASTER_KEY_KMS_AUTO_PROVISION": "true",
        }
        with patch.dict(os.environ, values, clear=False):
            config = ServerConfig.from_env()

        self.assertEqual("kms", config.master_key_source)
        self.assertEqual("alias/past-partner", config.master_key_kms_key_id)
        self.assertEqual("cn-test-1", config.master_key_kms_region)
        self.assertTrue(config.master_key_kms_auto_provision)

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

    def test_normalized_retention_is_disabled_by_default_and_configurable(self) -> None:
        with patch.dict(os.environ, {"PAST_PARTNER_NORMALIZED_RETENTION_SECONDS": "172800"}, clear=False):
            config = ServerConfig.from_env()

        self.assertEqual(172800, config.normalized_retention_seconds)
        self.assertEqual(0, ServerConfig().normalized_retention_seconds)

    def test_normalized_retention_rejects_values_above_policy_maximum(self) -> None:
        with self.assertRaisesRegex(ValueError, "retention"):
            ServerConfig(normalized_retention_seconds=5 * 365 * 24 * 60 * 60 + 1).validated()

    def test_env_template_exposes_disabled_raw_retention_setting(self) -> None:
        template = Path(__file__).parents[2] / ".env.example"

        self.assertIn("PAST_PARTNER_RAW_RETENTION_SECONDS=0", template.read_text(encoding="utf-8"))

    def test_model_pricing_json_is_loaded_from_environment(self) -> None:
        raw = '{"deepseek/deepseek-v4-flash":{"input_price_per_million_tokens":0.14}}'
        with patch.dict(os.environ, {"PAST_PARTNER_MODEL_PRICING_JSON": raw}, clear=False):
            config = ServerConfig.from_env()

        self.assertEqual(raw, config.model_pricing_json)
        self.assertIsNone(ServerConfig().model_pricing_json)

    def test_oidc_config_requires_complete_https_issuer_audience_and_jwks(self) -> None:
        with self.assertRaises(ValueError) as incomplete:
            ServerConfig(oidc_issuer="https://issuer.example").validated()
        self.assertEqual("oidc_configuration_incomplete", incomplete.exception.code)

        with self.assertRaises(ValueError) as insecure:
            ServerConfig(
                oidc_issuer="http://issuer.example",
                oidc_audience="past-partner",
                oidc_jwks_json='{"keys": []}',
            ).validated()
        self.assertEqual("oidc_issuer_invalid", insecure.exception.code)

        with self.assertRaises(ValueError) as malformed:
            ServerConfig(
                oidc_issuer="https://issuer.example",
                oidc_audience="past-partner",
                oidc_jwks_json="not-json",
            ).validated()
        self.assertEqual("oidc_jwks_invalid", malformed.exception.code)

    def test_oidc_config_is_loaded_from_environment(self) -> None:
        values = {
            "PAST_PARTNER_OIDC_ISSUER": "https://issuer.example",
            "PAST_PARTNER_OIDC_AUDIENCE": "past-partner",
            "PAST_PARTNER_OIDC_JWKS_JSON": '{"keys": []}',
        }
        with patch.dict(os.environ, values, clear=False):
            config = ServerConfig.from_env()

        self.assertEqual(values["PAST_PARTNER_OIDC_ISSUER"], config.oidc_issuer)
        self.assertEqual(values["PAST_PARTNER_OIDC_AUDIENCE"], config.oidc_audience)
        self.assertEqual(values["PAST_PARTNER_OIDC_JWKS_JSON"], config.oidc_jwks_json)

    def test_postgresql_backend_requires_dsn_and_normalizes_alias(self) -> None:
        with self.assertRaisesRegex(ValueError, "DSN") as captured:
            ServerConfig(metadata_backend="postgres").validated()
        self.assertEqual("metadata_dsn_required", captured.exception.code)

        config = ServerConfig(
            metadata_backend="postgres",
            metadata_dsn="postgresql://user:password@example.invalid/past_partner",
            metadata_pool_min_size=2,
            metadata_pool_max_size=8,
        ).validated()

        self.assertEqual("postgresql", config.metadata_backend)
        self.assertEqual(2, config.metadata_pool_min_size)
        self.assertEqual(8, config.metadata_pool_max_size)

    def test_postgresql_pool_bounds_are_rejected_without_echoing_dsn(self) -> None:
        dsn = "postgresql://user:password@example.invalid/past_partner"
        for minimum, maximum in ((0, 1), (3, 2), (1, 65)):
            with self.subTest(minimum=minimum, maximum=maximum):
                with self.assertRaises(ValueError) as captured:
                    ServerConfig(
                        metadata_backend="postgresql",
                        metadata_dsn=dsn,
                        metadata_pool_min_size=minimum,
                        metadata_pool_max_size=maximum,
                    ).validated()
                self.assertNotIn("password", str(captured.exception))
                self.assertNotIn("example.invalid", str(captured.exception))

    def test_postgresql_backend_from_env_reads_pool_settings(self) -> None:
        values = {
            "PAST_PARTNER_METADATA_BACKEND": "postgres",
            "PAST_PARTNER_METADATA_DSN": "postgresql://user:password@example.invalid/past_partner",
            "PAST_PARTNER_METADATA_POOL_MIN_SIZE": "2",
            "PAST_PARTNER_METADATA_POOL_MAX_SIZE": "6",
        }
        with patch.dict(os.environ, values, clear=False):
            config = ServerConfig.from_env()

        self.assertEqual("postgresql", config.metadata_backend)
        self.assertEqual(values["PAST_PARTNER_METADATA_DSN"], config.metadata_dsn)
        self.assertEqual(2, config.metadata_pool_min_size)
        self.assertEqual(6, config.metadata_pool_max_size)

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
