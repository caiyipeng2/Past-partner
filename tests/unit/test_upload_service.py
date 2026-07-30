import hashlib
import io
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.services.import_service import ImportService, ImportState
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
        personas = PersonaService(layout)
        imports = ImportService(layout, personas)
        persona = personas.create("小雨", "friend")
        self.job = imports.create(persona.id, "chat.txt", 11, "text/plain")
        self.imports = imports
        self.uploads = UploadService(layout, imports, read_block_bytes=4)

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
        self.assertEqual(first + second, self.uploads.payload_path(self.job.id).read_bytes())

    def test_identical_retry_is_idempotent(self) -> None:
        value = b"hello "
        first = self.uploads.put_chunk(self.job.id, 0, len(value), self.digest(value), io.BytesIO(value))
        retry = self.uploads.put_chunk(self.job.id, 0, len(value), self.digest(value), io.BytesIO(value))

        self.assertFalse(first.duplicate)
        self.assertTrue(retry.duplicate)
        self.assertEqual(1, self.imports.get(self.job.id).chunk_count)
        self.assertEqual(len(value), self.imports.get(self.job.id).received_bytes)

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
