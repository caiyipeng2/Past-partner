import base64
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.server.application import Application
from src.server.config import ServerConfig
from src.services.authenticated_encryption import (
    ENVELOPE_MAGIC,
    ENVELOPE_HEADER_BYTES,
    ENVELOPE_VERSION,
    DEFAULT_MAX_PLAINTEXT_BYTES,
    WRAPPED_DATA_KEY_BYTES,
    AuthenticationError,
    AuthenticatedEncryptionService,
    EncryptionConfigurationError,
    InvalidEncryptedPayloadError,
    master_key_identifier,
)
from src.services.master_key import (
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
    EnvironmentMasterKeyProvider,
    MasterKeyUnavailableError,
)


class SequenceRandom:
    def __init__(self, values: list[bytes]):
        self.values = list(values)
        self.requested: list[int] = []

    def __call__(self, length: int) -> bytes:
        self.requested.append(length)
        return self.values.pop(0)


def provider(key: bytes = b"m" * MASTER_KEY_BYTES) -> EnvironmentMasterKeyProvider:
    configured = base64.b64encode(key).decode("ascii")
    return EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: configured})


class AuthenticatedEncryptionTests(unittest.TestCase):
    def test_round_trips_binary_plaintext_with_required_aad(self) -> None:
        service = AuthenticatedEncryptionService(provider())
        plaintext = "聊天记录\x00附件".encode("utf-8")
        aad = b"persona:123/import:456"

        encrypted = service.encrypt(plaintext, aad)

        self.assertNotEqual(plaintext, encrypted)
        self.assertNotIn(plaintext, encrypted)
        self.assertEqual(plaintext, service.decrypt(encrypted, aad))

    def test_same_input_uses_fresh_data_keys_and_nonces(self) -> None:
        service = AuthenticatedEncryptionService(provider())

        first = service.encrypt(b"same", b"object:1")
        second = service.encrypt(b"same", b"object:1")

        self.assertNotEqual(first, second)
        self.assertEqual(b"same", service.decrypt(first, b"object:1"))
        self.assertEqual(b"same", service.decrypt(second, b"object:1"))

    def test_generates_one_data_key_and_two_96_bit_nonces_per_payload(self) -> None:
        random = SequenceRandom([b"d" * 32, b"w" * 12, b"p" * 12])
        service = AuthenticatedEncryptionService(provider(), random_bytes=random)

        encrypted = service.encrypt(b"secret", b"object:1")

        self.assertEqual([32, 12, 12], random.requested)
        self.assertNotIn(b"d" * 32, encrypted)
        self.assertEqual(b"secret", service.decrypt(encrypted, b"object:1"))

    def test_rejects_changed_aad_without_revealing_authentication_details(self) -> None:
        service = AuthenticatedEncryptionService(provider())
        encrypted = service.encrypt(b"secret", b"persona:1")

        with self.assertRaisesRegex(AuthenticationError, "authentication failed"):
            service.decrypt(encrypted, b"persona:2")

    def test_rejects_wrapped_key_and_payload_tampering(self) -> None:
        service = AuthenticatedEncryptionService(provider())
        encrypted = service.encrypt(b"secret", b"object:1")
        tamper_offsets = (6, ENVELOPE_HEADER_BYTES, len(encrypted) - 1)

        for offset in tamper_offsets:
            with self.subTest(offset=offset):
                tampered = bytearray(encrypted)
                tampered[offset] ^= 1
                with self.assertRaisesRegex(AuthenticationError, "authentication failed"):
                    service.decrypt(bytes(tampered), b"object:1")

    def test_wrong_master_key_has_the_same_authentication_error(self) -> None:
        encrypted = AuthenticatedEncryptionService(provider(b"a" * 32)).encrypt(
            b"secret", b"object:1"
        )

        with self.assertRaisesRegex(AuthenticationError, "authentication failed"):
            AuthenticatedEncryptionService(provider(b"b" * 32)).decrypt(
                encrypted, b"object:1"
            )

    def test_key_resolver_can_decrypt_an_envelope_after_key_rotation(self) -> None:
        old_key = b"a" * MASTER_KEY_BYTES
        new_key = b"b" * MASTER_KEY_BYTES
        old_key_id = master_key_identifier(old_key)
        encrypted = AuthenticatedEncryptionService(provider(old_key)).encrypt(
            b"secret", b"object:1"
        )
        requested_ids: list[bytes] = []

        def resolve(key_id: bytes) -> bytes:
            requested_ids.append(key_id)
            return {old_key_id: old_key}[key_id]

        rotated = AuthenticatedEncryptionService(
            provider(new_key), key_resolver=resolve
        )

        self.assertEqual(b"secret", rotated.decrypt(encrypted, b"object:1"))
        self.assertEqual([old_key_id], requested_ids)

    def test_limits_each_encryption_segment_instead_of_buffering_a_full_import(self) -> None:
        self.assertEqual(64 * 1024**2, DEFAULT_MAX_PLAINTEXT_BYTES)
        service = AuthenticatedEncryptionService(
            provider(), max_plaintext_bytes=4
        )

        with self.assertRaisesRegex(ValueError, "segment limit"):
            service.encrypt(b"12345", b"import:1/chunk:0/final:false")

        encrypted = AuthenticatedEncryptionService(
            provider(), max_plaintext_bytes=5
        ).encrypt(b"12345", b"import:1/chunk:0/final:false")
        with self.assertRaisesRegex(InvalidEncryptedPayloadError, "segment limit"):
            service.decrypt(encrypted, b"import:1/chunk:0/final:false")

    def test_rejects_empty_aad_and_non_bytes_inputs(self) -> None:
        service = AuthenticatedEncryptionService(provider())

        for plaintext, aad in ((b"secret", b""), ("secret", b"object:1")):
            with self.subTest(plaintext=plaintext, aad=aad):
                with self.assertRaises(TypeError):
                    service.encrypt(plaintext, aad)  # type: ignore[arg-type]

        with self.assertRaises(TypeError):
            service.decrypt(b"payload", "object:1")  # type: ignore[arg-type]

    def test_rejects_truncated_or_unsupported_envelopes(self) -> None:
        service = AuthenticatedEncryptionService(provider())
        minimum_size = ENVELOPE_HEADER_BYTES + WRAPPED_DATA_KEY_BYTES + 16
        invalid_envelopes = {
            "truncated": b"x" * (minimum_size - 1),
            "wrong_magic": b"FAIL" + bytes([ENVELOPE_VERSION, 1]) + b"x" * (minimum_size - 6),
            "wrong_version": ENVELOPE_MAGIC + bytes([ENVELOPE_VERSION + 1, 1]) + b"x" * (minimum_size - 6),
            "wrong_algorithm": ENVELOPE_MAGIC + bytes([ENVELOPE_VERSION, 99]) + b"x" * (minimum_size - 6),
        }

        for name, encrypted in invalid_envelopes.items():
            with self.subTest(name=name):
                with self.assertRaises(InvalidEncryptedPayloadError):
                    service.decrypt(encrypted, b"object:1")

    def test_rejects_random_sources_returning_wrong_lengths(self) -> None:
        service = AuthenticatedEncryptionService(
            provider(), random_bytes=lambda length: b"x" * (length - 1)
        )

        with self.assertRaisesRegex(EncryptionConfigurationError, "random source"):
            service.encrypt(b"secret", b"object:1")

    def test_missing_master_key_fails_before_encryption(self) -> None:
        service = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({})
        )

        with self.assertRaises(MasterKeyUnavailableError):
            service.encrypt(b"secret", b"object:1")

    def test_application_wires_the_encryption_service(self) -> None:
        configured = base64.b64encode(b"k" * MASTER_KEY_BYTES).decode("ascii")
        with patch.dict(os.environ, {MASTER_KEY_ENV_VAR: configured}), patch(
            "src.server.application.SQLiteMigrator.migrate"
        ):
            config = ServerConfig(
                data_dir=Path(".test-runtime/application-wiring-data"),
                web_dir=Path("web"),
                mode="test",
            )
            application = Application.from_config(config)

            encrypted = application.encryption.encrypt(b"secret", b"object:1")

            self.assertEqual(
                b"secret", application.encryption.decrypt(encrypted, b"object:1")
            )
