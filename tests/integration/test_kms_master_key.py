"""Opt-in integration contract for the KMS master-key lifecycle."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from src.services.master_key import AwsKmsBackend, KmsMasterKeyProvider, MASTER_KEY_BYTES


class _DisposableKmsBackend:
    def encrypt(self, key_id: str, plaintext: bytes) -> bytes:
        return b"kms-test:" + plaintext[::-1]

    def decrypt(self, key_id: str, ciphertext: bytes) -> bytes:
        if not ciphertext.startswith(b"kms-test:"):
            raise ValueError("invalid disposable ciphertext")
        return ciphertext[len(b"kms-test:") :][::-1]


class KmsMasterKeyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        if os.environ.get("PAST_PARTNER_KMS_TEST_DISPOSABLE") != "1":
            raise unittest.SkipTest("KMS integration requires an explicitly disposable key")
        self._endpoint = os.environ.get("PAST_PARTNER_KMS_TEST_ENDPOINT")
        self._key_id = os.environ.get("PAST_PARTNER_KMS_TEST_KEY_ID")
        if self._endpoint and self._key_id:
            try:
                import boto3
            except ImportError as exc:
                raise unittest.SkipTest("install requirements-storage.txt for live KMS verification") from exc
            client = boto3.client(
                "kms",
                region_name=os.environ.get("PAST_PARTNER_KMS_TEST_REGION", "us-east-1"),
                endpoint_url=self._endpoint,
                aws_access_key_id=os.environ.get("PAST_PARTNER_KMS_TEST_ACCESS_KEY"),
                aws_secret_access_key=os.environ.get("PAST_PARTNER_KMS_TEST_SECRET_KEY"),
                aws_session_token=os.environ.get("PAST_PARTNER_KMS_TEST_SESSION_TOKEN"),
            )
            self._backend = AwsKmsBackend(
                client=client,
            )
            return
        if os.environ.get("PAST_PARTNER_KMS_TEST_ENABLED") != "1":
            raise unittest.SkipTest(
                "live KMS test requires PAST_PARTNER_KMS_TEST_ENDPOINT and PAST_PARTNER_KMS_TEST_KEY_ID"
            )
        self._backend = _DisposableKmsBackend()
        self._key_id = "alias/disposable-test"

    def test_disposable_kms_round_trip_persists_only_ciphertext(self) -> None:
        expected = b"z" * MASTER_KEY_BYTES
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / "secrets" / "master-key.kms"
            first = KmsMasterKeyProvider(
                key_path,
                key_id=self._key_id,
                backend=self._backend,
                auto_provision=True,
                random_bytes=lambda length: expected,
            )
            self.assertEqual(expected, first.key_for_sensitive_write())
            self.assertNotIn(expected, key_path.read_bytes())

            second = KmsMasterKeyProvider(
                key_path,
                key_id=self._key_id,
                backend=self._backend,
            )
            self.assertEqual(expected, second.key_for_sensitive_write())


if __name__ == "__main__":
    unittest.main()
