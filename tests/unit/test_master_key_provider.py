import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.server.application import Application
from src.server.config import ServerConfig
from src.services.master_key import (
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
    EnvironmentMasterKeyProvider,
    MasterKeyConfigurationError,
    MasterKeyUnavailableError,
    WindowsDataProtection,
    WindowsDpapiMasterKeyProvider,
    build_master_key_provider,
)


class RecordingDpapiBackend:
    def protect(self, value: bytes) -> bytes:
        return b"dpapi:" + value[::-1]

    def unprotect(self, value: bytes) -> bytes:
        if not value.startswith(b"dpapi:"):
            raise ValueError("invalid protected payload")
        return value[len(b"dpapi:") :][::-1]


class MasterKeyProviderTests(unittest.TestCase):
    def test_environment_provider_returns_a_32_byte_base64_key(self) -> None:
        expected = bytes(range(MASTER_KEY_BYTES))
        provider = EnvironmentMasterKeyProvider(
            {MASTER_KEY_ENV_VAR: base64.b64encode(expected).decode("ascii")}
        )
        self.assertEqual(expected, provider.key_for_sensitive_write())

    def test_environment_provider_fails_closed_when_key_is_missing(self) -> None:
        with self.assertRaises(MasterKeyUnavailableError):
            EnvironmentMasterKeyProvider({}).key_for_sensitive_write()

    def test_environment_provider_rejects_malformed_values(self) -> None:
        provider = EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: "not-base64!"})
        with self.assertRaises(MasterKeyConfigurationError) as captured:
            provider.key_for_sensitive_write()
        self.assertNotIn("not-base64!", str(captured.exception))

    def test_environment_provider_rejects_wrong_length_values(self) -> None:
        configured = base64.b64encode(b"short").decode("ascii")
        provider = EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: configured})
        with self.assertRaises(MasterKeyConfigurationError) as captured:
            provider.key_for_sensitive_write()
        self.assertNotIn(configured, str(captured.exception))

    def test_dpapi_provider_requires_explicit_provisioning_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = WindowsDpapiMasterKeyProvider(
                Path(directory) / "master-key.dpapi",
                backend=RecordingDpapiBackend(),
                auto_provision=False,
            )
            with self.assertRaises(MasterKeyUnavailableError):
                provider.key_for_sensitive_write()

    def test_dpapi_provider_persists_only_protected_key_material(self) -> None:
        expected = bytes(range(MASTER_KEY_BYTES))
        backend = RecordingDpapiBackend()
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "secrets" / "master-key.dpapi"
            first = WindowsDpapiMasterKeyProvider(
                key_path,
                backend=backend,
                auto_provision=True,
                random_bytes=lambda length: expected,
            )
            self.assertEqual(expected, first.key_for_sensitive_write())
            protected = key_path.read_bytes()
            self.assertNotEqual(expected, protected)
            self.assertNotIn(expected, protected)

            second = WindowsDpapiMasterKeyProvider(key_path, backend=backend)
            self.assertEqual(expected, second.key_for_sensitive_write())

    def test_dpapi_concurrent_provision_loads_the_winning_key(self) -> None:
        generated = b"g" * MASTER_KEY_BYTES
        winner = b"w" * MASTER_KEY_BYTES
        backend = RecordingDpapiBackend()
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "master-key.dpapi"
            provider = WindowsDpapiMasterKeyProvider(
                key_path,
                backend=backend,
                auto_provision=True,
                random_bytes=lambda length: generated,
            )

            def finish_competing_write(source: object, destination: object) -> None:
                Path(destination).write_bytes(backend.protect(winner))
                raise FileExistsError

            with patch("src.services.master_key.os.link", side_effect=finish_competing_write):
                self.assertEqual(winner, provider.key_for_sensitive_write())

    def test_dpapi_provider_rejects_unprotected_wrong_length_key(self) -> None:
        backend = RecordingDpapiBackend()
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "master-key.dpapi"
            key_path.write_bytes(backend.protect(b"short"))
            provider = WindowsDpapiMasterKeyProvider(key_path, backend=backend)

            with self.assertRaises(MasterKeyConfigurationError):
                provider.key_for_sensitive_write()

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI is platform-specific")
    def test_native_windows_dpapi_round_trip(self) -> None:
        expected = b"w" * MASTER_KEY_BYTES
        backend = WindowsDataProtection()

        protected = backend.protect(expected)

        self.assertNotEqual(expected, protected)
        self.assertEqual(expected, backend.unprotect(protected))

    def test_factory_auto_provisions_dpapi_only_for_windows_development(self) -> None:
        expected = b"d" * MASTER_KEY_BYTES
        with tempfile.TemporaryDirectory() as directory:
            provider = build_master_key_provider(
                Path(directory),
                mode="development",
                environ={},
                platform_name="nt",
                dpapi_backend=RecordingDpapiBackend(),
                random_bytes=lambda length: expected,
            )

            self.assertEqual(expected, provider.key_for_sensitive_write())
            self.assertTrue((Path(directory) / "secrets" / "master-key.dpapi").is_file())

    def test_factory_prefers_injected_environment_key_in_production(self) -> None:
        expected = b"e" * MASTER_KEY_BYTES
        with tempfile.TemporaryDirectory() as directory:
            provider = build_master_key_provider(
                Path(directory),
                mode="production",
                environ={MASTER_KEY_ENV_VAR: base64.b64encode(expected).decode("ascii")},
                platform_name="nt",
            )
            self.assertEqual(expected, provider.key_for_sensitive_write())

    def test_factory_fails_sensitive_write_without_a_production_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            provider = build_master_key_provider(
                Path(directory), mode="production", environ={}, platform_name="nt"
            )
            with self.assertRaises(MasterKeyUnavailableError):
                provider.key_for_sensitive_write()

    def test_application_wires_the_runtime_master_key_provider(self) -> None:
        expected = b"a" * MASTER_KEY_BYTES
        configured = base64.b64encode(expected).decode("ascii")
        with tempfile.TemporaryDirectory() as directory:
            config = ServerConfig(
                data_dir=Path(directory),
                web_dir=Path.cwd() / "web",
                mode="test",
            )
            with patch.dict(os.environ, {MASTER_KEY_ENV_VAR: configured}, clear=True):
                application = Application.from_config(config)

        self.assertEqual(expected, application.master_keys.key_for_sensitive_write())


if __name__ == "__main__":
    unittest.main()
