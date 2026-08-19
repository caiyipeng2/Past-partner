"""Opt-in integration contract for the KMS master-key lifecycle."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.services.master_key import KmsMasterKeyProvider, MASTER_KEY_BYTES


class _DisposableKmsBackend:
    def encrypt(self, key_id: str, plaintext: bytes) -> bytes:
        return b"kms-test:" + plaintext[::-1]

    def decrypt(self, key_id: str, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(b"kms-test:"):
            raise ValueError("invalid disposable ciphertext")
        return ciphertext[len(b"kms-test:") :][::-1]


class KmsMasterKeyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        if os.environ.get("PAST_PARTNER_KMS_TEST_ENABLED") != "1":
            raise unittest.SkipTest("KMS integration requires explicit opt-in")

    def test_disposable_kms_round_trip_persists_only_ciphertext(self) -> None:
        expected = b"z" * MASTER_KEY_BYTES
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "secrets" / "master-key.kms"
            first = KmsMasterKeyProvider(
                key_path,
                key_id="alias/disposable-test",
                backend=_DisposableKmsBackend(),
                auto_provision=True,
                random_bytes=lambda length: expected,
            )
            self.assertEqual(expected, first.key_for_sensitive_write())
            self.assertNotIn(expected, key_path.read_bytes())

            second = KmsMasterKeyProvider(
                key_path,
                key_id="alias/disposable-test",
                backend=_DisposableKmsBackend(),
            )
            self.assertEqual(expected, second.key_for_sensitive_write())


if __name__ == "__main__":
    unittest.main()
