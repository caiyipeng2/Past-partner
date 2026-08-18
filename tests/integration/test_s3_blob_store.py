"""Opt-in live contract test for a disposable S3-compatible bucket."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from src.services.blob_store import S3BlobStoreSettings, build_blob_store
from src.services.storage import StorageLayout


class S3BlobStoreIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        required = (
            "PAST_PARTNER_S3_TEST_ENDPOINT",
            "PAST_PARTNER_S3_TEST_BUCKET",
            "PAST_PARTNER_S3_TEST_ACCESS_KEY",
            "PAST_PARTNER_S3_TEST_SECRET_KEY",
        )
        if os.environ.get("PAST_PARTNER_S3_TEST_DISPOSABLE") != "1":
            raise unittest.SkipTest("live S3 test requires an explicitly disposable bucket")
        if any(not os.environ.get(name) for name in required):
            raise unittest.SkipTest("live S3 test endpoint and credentials are not configured")
        try:
            import boto3  # noqa: F401
        except ImportError as exc:
            raise unittest.SkipTest("install requirements-storage.txt for live S3 verification") from exc

        cls._temporary = tempfile.TemporaryDirectory(prefix="past-partner-s3-test-")
        settings = S3BlobStoreSettings(
            endpoint=os.environ["PAST_PARTNER_S3_TEST_ENDPOINT"],
            bucket=os.environ["PAST_PARTNER_S3_TEST_BUCKET"],
            region=os.environ.get("PAST_PARTNER_S3_TEST_REGION", "us-east-1"),
            access_key=os.environ["PAST_PARTNER_S3_TEST_ACCESS_KEY"],
            secret_key=os.environ["PAST_PARTNER_S3_TEST_SECRET_KEY"],
            session_token=os.environ.get("PAST_PARTNER_S3_TEST_SESSION_TOKEN"),
            path_style=os.environ.get("PAST_PARTNER_S3_TEST_PATH_STYLE", "true").casefold() == "true",
        )
        cls._key = f"p4-04-test/{uuid4().hex}.bin"
        cls._store = build_blob_store(
            "s3",
            StorageLayout(Path(cls._temporary.name)),
            s3_settings=settings,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        store = getattr(cls, "_store", None)
        key = getattr(cls, "_key", None)
        if store is not None and key is not None:
            store.delete(key)
        temporary = getattr(cls, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()

    def test_round_trip_uses_real_s3_compatible_endpoint(self) -> None:
        payload = b"P4-04 live S3 contract"
        digest = hashlib.sha256(payload).hexdigest()
        receipt = self._store.put(
            self._key,
            io.BytesIO(payload),
            length=len(payload),
            sha256=digest,
        )

        self.assertEqual(self._key, receipt.key)
        self.assertTrue(self._store.exists(self._key))
        self.assertEqual(payload, b"".join(self._store.iter_bytes(self._key, block_bytes=5)))
        self.assertTrue(self._store.delete(self._key))
        self.assertFalse(self._store.exists(self._key))


if __name__ == "__main__":
    unittest.main()
