import base64
import hashlib
import io
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.import_repository import ImportRepository
from src.services.import_service import ImportService, ImportState
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR, EnvironmentMasterKeyProvider
from src.services.persona_repository import PersonaRepository
from src.services.persona_service import PersonaService
from src.services.storage import StorageLayout
from src.services.upload_service import UploadError, UploadService


class RecordingReader(io.BytesIO):
    def __init__(self, value: bytes):
        super().__init__(value)
        self.requested_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.requested_sizes.append(size)
        return super().read(size)


class UploadServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"u" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        personas = PersonaService(
            PersonaRepository(layout.database_path(), self.encryption)
        )
        imports = ImportService(ImportRepository(layout.database_path(), self.encryption), personas)
        persona = personas.create("小雨", "friend")
        self.job = imports.create(persona.id, "chat.txt", 11, "text/plain")
        self.imports = imports
        self.uploads = UploadService(layout, imports, self.encryption, read_block_bytes=4)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def test_accepts_out_of_order_chunks_and_completes_payload(self) -> None:
        second = b"world"
        first = b"hello "
        self.uploads.put_chunk(self.job.id, 1, len(second), self.digest(second), io.BytesIO(second))
        self.uploads.put_chunk(self.job.id, 0, len(first), self.digest(first), io.BytesIO(first))

        completed = self.uploads.complete(self.job.id, self.digest(first + second))

        self.assertEqual(ImportState.UPLOADED, completed.state)
        encrypted_payload = self.uploads.payload_path(self.job.id).read_bytes()
        self.assertNotEqual(first + second, encrypted_payload)
        self.assertEqual(first + second, b"".join(self.uploads.iter_payload(self.job.id)))
        self.assertFalse((self.root / "upload-manifests").exists())
        self.assertNotIn(b"encrypted_length", (self.root / "database" / "past-partner.sqlite3").read_bytes())

    def test_chunks_and_completed_payload_are_authenticated_envelopes(self) -> None:
        value = b"secret"
        self.uploads.put_chunk(
            self.job.id, 0, len(value), self.digest(value), io.BytesIO(value)
        )

        stored_chunk = self.uploads._chunk_path(self.job.id, 0).read_bytes()

        self.assertNotIn(value, stored_chunk)
        self.assertEqual(
            value,
            self.encryption.decrypt(
                stored_chunk, self.uploads.chunk_aad(self.job.id, 0, final=False)
            ),
        )

    def test_tampered_chunk_fails_completion_before_payload_replace(self) -> None:
        value = b"secret"
        filler = b"xxxxx"
        self.uploads.put_chunk(
            self.job.id, 0, len(value), self.digest(value), io.BytesIO(value)
        )
        self.uploads.put_chunk(
            self.job.id, 1, len(filler), self.digest(filler), io.BytesIO(filler)
        )
        path = self.uploads._chunk_path(self.job.id, 0)
        tampered = bytearray(path.read_bytes())
        tampered[0] ^= 1
        path.write_bytes(tampered)

        with self.assertRaises(UploadError) as captured:
            self.uploads.complete(self.job.id)

        self.assertEqual("chunk_authentication_failed", captured.exception.code)
        self.assertFalse(self.uploads.payload_path(self.job.id).exists())

    def test_chunk_trailing_bytes_fail_closed(self) -> None:
        value = b"secret"
        filler = b"xxxxx"
        self.uploads.put_chunk(
            self.job.id, 0, len(value), self.digest(value), io.BytesIO(value)
        )
        self.uploads.put_chunk(
            self.job.id, 1, len(filler), self.digest(filler), io.BytesIO(filler)
        )
        path = self.uploads._chunk_path(self.job.id, 0)
        with path.open("ab") as output:
            output.write(b"trailing")

        with self.assertRaises(UploadError) as captured:
            self.uploads.complete(self.job.id)

        self.assertEqual("chunk_corrupt", captured.exception.code)

    def test_tampered_final_sentinel_is_rejected_when_reading_payload(self) -> None:
        value = b"secret"
        filler = b"xxxxx"
        self.uploads.put_chunk(
            self.job.id, 0, len(value), self.digest(value), io.BytesIO(value)
        )
        self.uploads.put_chunk(
            self.job.id, 1, len(filler), self.digest(filler), io.BytesIO(filler)
        )
        self.uploads.complete(self.job.id)
        path = self.uploads.payload_path(self.job.id)
        tampered = bytearray(path.read_bytes())
        tampered[-1] ^= 1
        path.write_bytes(tampered)

        with self.assertRaises(UploadError) as captured:
            list(self.uploads.iter_payload(self.job.id))

        self.assertEqual("payload_authentication_failed", captured.exception.code)

    def test_identical_retry_is_idempotent(self) -> None:
        value = b"hello "
        first = self.uploads.put_chunk(self.job.id, 0, len(value), self.digest(value), io.BytesIO(value))
        retry = self.uploads.put_chunk(self.job.id, 0, len(value), self.digest(value), io.BytesIO(value))

        self.assertFalse(first.duplicate)
        self.assertTrue(retry.duplicate)
        self.assertEqual(1, self.imports.get(self.job.id).chunk_count)
        self.assertEqual(len(value), self.imports.get(self.job.id).received_bytes)

    def test_reports_received_and_missing_chunk_indexes(self) -> None:
        first = b"hello"
        last = b"ok"
        self.uploads.put_chunk(self.job.id, 0, len(first), self.digest(first), io.BytesIO(first))
        self.uploads.put_chunk(self.job.id, 2, len(last), self.digest(last), io.BytesIO(last))

        status = self.uploads.missing_chunks(self.job.id, expected_chunks=3)

        self.assertEqual(self.job.id, status["import_id"])
        self.assertEqual("uploading", status["state"])
        self.assertEqual(11, status["total_bytes"])
        self.assertEqual(7, status["received_bytes"])
        self.assertEqual(3, status["expected_chunk_count"])
        self.assertEqual([0, 2], status["received_chunks"])
        self.assertEqual([1], status["missing_chunks"])

    def test_cancel_clears_stored_parts_and_closes_the_import(self) -> None:
        value = b"secret"
        self.uploads.put_chunk(self.job.id, 0, len(value), self.digest(value), io.BytesIO(value))
        chunk_path = self.uploads._chunk_path(self.job.id, 0)
        self.assertTrue(chunk_path.exists())

        cancelled = self.uploads.cancel(self.job.id)

        self.assertEqual(ImportState.CANCELLED, cancelled.state)
        self.assertFalse(chunk_path.exists())
        self.assertEqual(0, cancelled.received_bytes)
        self.assertEqual(0, cancelled.chunk_count)
        self.assertEqual({}, self.imports.get_manifest(self.job.id)["chunks"])
        with self.assertRaises(UploadError) as captured:
            self.uploads.put_chunk(
                self.job.id, 0, len(value), self.digest(value), io.BytesIO(value)
            )
        self.assertEqual("upload_closed", captured.exception.code)
        with self.assertRaises(UploadError) as captured:
            self.uploads.complete(self.job.id)
        self.assertEqual("upload_closed", captured.exception.code)

    def test_cancel_is_idempotent_for_an_already_cancelled_import(self) -> None:
        first = self.uploads.cancel(self.job.id)

        second = self.uploads.cancel(self.job.id)

        self.assertEqual(ImportState.CANCELLED, first.state)
        self.assertEqual(first, second)

    def test_missing_chunk_status_rejects_expected_count_below_stored_index(self) -> None:
        value = b"hello"
        self.uploads.put_chunk(self.job.id, 2, len(value), self.digest(value), io.BytesIO(value))

        with self.assertRaises(UploadError) as captured:
            self.uploads.missing_chunks(self.job.id, expected_chunks=2)

        self.assertEqual("invalid_expected_chunk_count", captured.exception.code)

    def test_conflicting_retry_is_rejected(self) -> None:
        value = b"hello "
        self.uploads.put_chunk(self.job.id, 0, len(value), self.digest(value), io.BytesIO(value))

        replacement = b"HELLO!"
        with self.assertRaises(UploadError) as captured:
            self.uploads.put_chunk(
                self.job.id,
                0,
                len(replacement),
                self.digest(replacement),
                io.BytesIO(replacement),
            )
        self.assertEqual("chunk_conflict", captured.exception.code)

    def test_rejects_digest_mismatch_and_total_overflow(self) -> None:
        with self.assertRaises(UploadError) as captured:
            self.uploads.put_chunk(self.job.id, 0, 6, "0" * 64, io.BytesIO(b"hello "))
        self.assertEqual("chunk_digest_mismatch", captured.exception.code)

        too_large = b"x" * 12
        with self.assertRaises(UploadError) as captured:
            self.uploads.put_chunk(
                self.job.id,
                0,
                len(too_large),
                self.digest(too_large),
                io.BytesIO(too_large),
            )
        self.assertEqual("import_size_exceeded", captured.exception.code)

    def test_incomplete_upload_cannot_complete(self) -> None:
        value = b"hello "
        self.uploads.put_chunk(self.job.id, 0, len(value), self.digest(value), io.BytesIO(value))

        with self.assertRaises(UploadError) as captured:
            self.uploads.complete(self.job.id)
        self.assertEqual("upload_incomplete", captured.exception.code)

    def test_reads_stream_in_bounded_blocks(self) -> None:
        value = b"hello "
        reader = RecordingReader(value)

        self.uploads.put_chunk(self.job.id, 0, len(value), self.digest(value), reader)

        self.assertGreater(len(reader.requested_sizes), 1)
        self.assertLessEqual(max(reader.requested_sizes), 4)


if __name__ == "__main__":
    unittest.main()
