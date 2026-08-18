import inspect
import io
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from src.services.blob_store import (
    BlobReceipt,
    BlobStore,
    LocalBlobStore,
    S3BlobStore,
    S3BlobStoreSettings,
    StorageBackendUnavailableError,
    StorageError,
    build_blob_store,
)
from src.services.storage import StorageLayout


class _FakeBlobStore:
    def put(
        self,
        key: str,
        source: io.BytesIO,
        *,
        length: int,
        sha256: str,
    ) -> BlobReceipt:
        return BlobReceipt(key=key, length=length, sha256=sha256)

    def iter_bytes(self, key: str, *, block_bytes: int) -> Iterator[bytes]:
        yield b""

    def exists(self, key: str) -> bool:
        return False

    def delete(self, key: str) -> bool:
        return False


class BlobStoreContractTests(unittest.TestCase):
    def test_blob_receipt_is_immutable_and_contains_only_logical_metadata(self) -> None:
        receipt = BlobReceipt(key="payloads/import-1.bin", length=3, sha256="a" * 64)

        self.assertEqual("payloads/import-1.bin", receipt.key)
        self.assertEqual(3, receipt.length)
        self.assertEqual("a" * 64, receipt.sha256)
        with self.assertRaises(AttributeError):
            receipt.key = "absolute-path"
        self.assertEqual(
            {"key", "length", "sha256"},
            set(getattr(BlobReceipt, "__dataclass_fields__")),
        )

    def test_storage_error_exposes_stable_code_without_sensitive_context(self) -> None:
        secret = "provider-secret-should-not-escape"
        absolute_path = "C:/private/runtime/object.bin"
        error = StorageError("storage_write_failed", "object could not be stored")

        self.assertEqual("storage_write_failed", error.code)
        self.assertEqual("object could not be stored", str(error))
        self.assertNotIn(secret, str(error))
        self.assertNotIn(absolute_path, str(error))

    def test_blob_store_protocol_has_the_approved_callable_shape(self) -> None:
        self.assertIsInstance(_FakeBlobStore(), BlobStore)

        put_parameters = inspect.signature(BlobStore.put).parameters
        iter_parameters = inspect.signature(BlobStore.iter_bytes).parameters
        self.assertEqual(("self", "key", "source", "length", "sha256"), tuple(put_parameters))
        self.assertEqual(("self", "key", "block_bytes"), tuple(iter_parameters))
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, put_parameters["length"].kind)
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, put_parameters["sha256"].kind)
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, iter_parameters["block_bytes"].kind)

    def test_fake_blob_store_can_be_used_without_storage_layout(self) -> None:
        store: BlobStore = _FakeBlobStore()

        self.assertEqual(
            BlobReceipt(key="payloads/import-1.bin", length=0, sha256="b" * 64),
            store.put("payloads/import-1.bin", io.BytesIO(), length=0, sha256="b" * 64),
        )
        self.assertEqual([b""], list(store.iter_bytes("payloads/import-1.bin", block_bytes=8)))
        self.assertFalse(store.exists("payloads/import-1.bin"))
        self.assertFalse(store.delete("payloads/import-1.bin"))


class _FailingSource(io.BytesIO):
    def read(self, size: int = -1) -> bytes:
        raise OSError("source failure must not escape")


class LocalBlobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.store = LocalBlobStore(StorageLayout(self.root))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def test_put_iter_exists_and_delete_use_logical_keys(self) -> None:
        payload = b"hello blob store"
        key = "payloads/import-1.bin"

        receipt = self.store.put(
            key,
            io.BytesIO(payload),
            length=len(payload),
            sha256=self._digest(payload),
        )

        self.assertEqual(BlobReceipt(key=key, length=len(payload), sha256=self._digest(payload)), receipt)
        self.assertTrue(self.store.exists(key))
        self.assertEqual([b"hello", b" blob", b" stor", b"e"], list(self.store.iter_bytes(key, block_bytes=5)))
        self.assertTrue(self.store.delete(key))
        self.assertFalse(self.store.exists(key))
        self.assertFalse(self.store.delete(key))

    def test_rejects_unsafe_logical_keys(self) -> None:
        invalid_keys = (
            "",
            "/absolute/object.bin",
            "\\absolute\\object.bin",
            "C:/absolute/object.bin",
            "payloads/../object.bin",
            "payloads/./object.bin",
            "payloads//object.bin",
            "payloads/object\\name.bin",
            "payloads/object\x00.bin",
        )

        for key in invalid_keys:
            with self.subTest(key=key):
                with self.assertRaisesRegex(StorageError, "invalid") as captured:
                    self.store.exists(key)
                self.assertEqual("invalid_key", captured.exception.code)

    def test_rejects_length_or_digest_mismatch_without_committing_target(self) -> None:
        payload = b"verified bytes"
        key = "payloads/mismatch.bin"

        with self.assertRaisesRegex(StorageError, "length") as length_error:
            self.store.put(key, io.BytesIO(payload), length=len(payload) + 1, sha256=self._digest(payload))
        self.assertEqual("storage_write_failed", length_error.exception.code)
        self.assertFalse(self.store.exists(key))

        with self.assertRaisesRegex(StorageError, "digest") as digest_error:
            self.store.put(key, io.BytesIO(payload), length=len(payload), sha256="a" * 64)
        self.assertEqual("storage_write_failed", digest_error.exception.code)
        self.assertFalse(self.store.exists(key))

    def test_duplicate_put_reports_conflict_and_preserves_existing_object(self) -> None:
        key = "payloads/conflict.bin"
        original = b"original"
        replacement = b"replacement"
        self.store.put(key, io.BytesIO(original), length=len(original), sha256=self._digest(original))

        with self.assertRaisesRegex(StorageError, "already exists") as captured:
            self.store.put(
                key,
                io.BytesIO(replacement),
                length=len(replacement),
                sha256=self._digest(replacement),
            )

        self.assertEqual("object_conflict", captured.exception.code)
        self.assertEqual([original], list(self.store.iter_bytes(key, block_bytes=1024)))

    def test_source_or_atomic_write_failure_cleans_temporary_files(self) -> None:
        key = "payloads/failure.bin"
        with self.assertRaisesRegex(StorageError, "read") as read_error:
            self.store.put(key, _FailingSource(), length=1, sha256="a" * 64)
        self.assertEqual("storage_read_failed", read_error.exception.code)
        self.assertEqual([], list(self.root.rglob("*.tmp")))

        payload = b"old object"
        self.store.put(key, io.BytesIO(payload), length=len(payload), sha256=self._digest(payload))
        with patch("src.services.blob_store.os.replace", side_effect=OSError("replace failure")):
            with self.assertRaisesRegex(StorageError, "written") as write_error:
                self.store.put(
                    "payloads/other.bin",
                    io.BytesIO(b"new object"),
                    length=len(b"new object"),
                    sha256=self._digest(b"new object"),
                )
        self.assertEqual("storage_write_failed", write_error.exception.code)
        self.assertEqual([], list(self.root.rglob("*.tmp")))
        self.assertEqual([payload], list(self.store.iter_bytes(key, block_bytes=1024)))

    def test_adapter_errors_do_not_echo_root_or_temporary_names(self) -> None:
        secret = "provider-key"
        key = "payloads/" + secret + ".bin"
        with self.assertRaisesRegex(StorageError, "length") as captured:
            self.store.put(key, io.BytesIO(b"secret body"), length=999, sha256="a" * 64)

        self.assertNotIn(str(self.root), str(captured.exception))
        self.assertNotIn(secret, str(captured.exception))
        self.assertNotIn("secret body", str(captured.exception))


class _RemoteError(Exception):
    def __init__(self, status: int, code: str = "RemoteFailure") -> None:
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code},
        }
        super().__init__("remote secret and endpoint must not escape")


class _S3Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def iter_chunks(self, chunk_size: int):
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset : offset + chunk_size]

    def close(self) -> None:
        return None


class _FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.put_read_sizes: list[int] = []
        self.fail_head: Exception | None = None
        self.fail_get: Exception | None = None

    def put_object(self, *, Bucket: str, Key: str, Body, ContentLength: int, Metadata, IfNoneMatch: str):
        if Key in self.objects:
            raise _RemoteError(412, "PreconditionFailed")
        chunks: list[bytes] = []
        while True:
            chunk = Body.read(7)
            self.put_read_sizes.append(len(chunk))
            if not chunk:
                break
            chunks.append(chunk)
        self.objects[Key] = (b"".join(chunks), dict(Metadata))

    def get_object(self, *, Bucket: str, Key: str):
        if self.fail_get is not None:
            raise self.fail_get
        if Key not in self.objects:
            raise _RemoteError(404, "NoSuchKey")
        return {"Body": _S3Body(self.objects[Key][0])}

    def head_object(self, *, Bucket: str, Key: str):
        if self.fail_head is not None:
            raise self.fail_head
        if Key not in self.objects:
            raise _RemoteError(404, "NotFound")
        return {"ContentLength": len(self.objects[Key][0])}

    def delete_object(self, *, Bucket: str, Key: str):
        self.objects.pop(Key, None)


class S3BlobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _FakeS3Client()
        self.store = S3BlobStore(
            self.client,
            S3BlobStoreSettings(
                bucket="past-partner-test",
                region="local",
                endpoint="http://127.0.0.1:9000",
                path_style=True,
            ),
        )

    @staticmethod
    def _digest(payload: bytes) -> str:
        return hashlib.sha256(payload).hexdigest()

    def test_put_iter_exists_and_delete_use_bounded_client_calls(self) -> None:
        payload = b"s3 compatible object"
        key = "payloads/import-1.bin"

        receipt = self.store.put(key, io.BytesIO(payload), length=len(payload), sha256=self._digest(payload))

        self.assertEqual(BlobReceipt(key=key, length=len(payload), sha256=self._digest(payload)), receipt)
        self.assertTrue(self.store.exists(key))
        self.assertEqual([b"s3 com", b"patibl", b"e obje", b"ct"], list(self.store.iter_bytes(key, block_bytes=6)))
        self.assertTrue(all(size <= 7 for size in self.client.put_read_sizes))
        self.assertTrue(self.store.delete(key))
        self.assertFalse(self.store.delete(key))

    def test_s3_rejects_duplicate_and_cleans_digest_mismatch(self) -> None:
        key = "payloads/conflict.bin"
        original = b"original"
        self.store.put(key, io.BytesIO(original), length=len(original), sha256=self._digest(original))

        with self.assertRaisesRegex(StorageError, "already exists") as conflict:
            self.store.put(key, io.BytesIO(b"replacement"), length=11, sha256=self._digest(b"replacement"))
        self.assertEqual("object_conflict", conflict.exception.code)

        mismatch_key = "payloads/mismatch.bin"
        with self.assertRaisesRegex(StorageError, "digest") as mismatch:
            self.store.put(mismatch_key, io.BytesIO(b"body"), length=4, sha256="a" * 64)
        self.assertEqual("storage_write_failed", mismatch.exception.code)
        self.assertNotIn(mismatch_key, self.client.objects)

    def test_s3_maps_remote_errors_without_echoing_provider_details(self) -> None:
        missing = "payloads/missing.bin"
        with self.assertRaisesRegex(StorageError, "not found") as captured:
            list(self.store.iter_bytes(missing, block_bytes=16))
        self.assertEqual("object_not_found", captured.exception.code)
        self.assertNotIn("remote secret", str(captured.exception))

        self.client.fail_head = _RemoteError(503)
        with self.assertRaisesRegex(StorageError, "read") as head_error:
            self.store.exists("payloads/any.bin")
        self.assertEqual("storage_read_failed", head_error.exception.code)

    def test_s3_rejects_invalid_keys_before_remote_calls(self) -> None:
        with self.assertRaisesRegex(StorageError, "invalid") as captured:
            self.store.exists("../escape.bin")
        self.assertEqual("invalid_key", captured.exception.code)
        self.assertEqual({}, self.client.objects)

    def test_factory_fails_closed_when_optional_sdk_is_missing(self) -> None:
        settings = S3BlobStoreSettings(bucket="past-partner-test")
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {"boto3": None, "botocore": None, "botocore.config": None}):
                with self.assertRaises(StorageBackendUnavailableError) as captured:
                    build_blob_store("s3", StorageLayout(Path(directory)), s3_settings=settings)

        self.assertEqual("storage_backend_unavailable", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
