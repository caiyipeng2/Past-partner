import inspect
import io
import unittest
from typing import Iterator

from src.services.blob_store import BlobReceipt, BlobStore, StorageError


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


if __name__ == "__main__":
    unittest.main()
