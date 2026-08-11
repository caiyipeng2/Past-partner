import base64
from contextlib import contextmanager
import hashlib
import io
import shutil
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.import_repository import ImportRepository
from src.preprocessing.media_inspector import MediaInspectionError
from src.services.import_service import ImportNotFoundError, ImportService, ImportState
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


class BlockingMediaInspector:
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release

    def inspect(self, source: Path, declared_media_type: str) -> dict[str, object]:
        self.started.set()
        if not self.release.wait(timeout=5):
            raise AssertionError("media inspection test did not release")
        return {
            "kind": "image",
            "detected_media_type": declared_media_type,
            "format": "PNG",
            "dimensions": {"width": 1, "height": 1},
            "size_bytes": source.stat().st_size,
            "provider_transfer": False,
        }


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

    def test_stale_training_source_cleanup_failure_blocks_training_reads(self) -> None:
        """Known plaintext cleanup failures cannot be ignored by a training caller."""
        directory = self.root / "training-source"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "orphan.bin").write_bytes(b"sensitive")

        with patch(
            "src.services.upload_service.PlaintextLeaseRegistry.delete_if_stale",
            side_effect=OSError("locked"),
        ):
            blocked = UploadService(
                self.uploads.storage,
                self.imports,
                self.encryption,
                read_block_bytes=4,
            )
            with self.assertRaises(UploadError) as captured:
                blocked.iter_training_records("owner", "missing-import")

        self.assertEqual("training_dataset_cleanup_failed", captured.exception.code)

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

    def test_reports_authoritative_progress(self) -> None:
        value = b"hello"
        self.uploads.put_chunk(self.job.id, 0, len(value), self.digest(value), io.BytesIO(value))

        progress = self.uploads.progress(self.job.id)

        self.assertEqual(self.job.id, progress["import_id"])
        self.assertEqual("uploading", progress["state"])
        self.assertEqual(11, progress["total_bytes"])
        self.assertEqual(5, progress["received_bytes"])
        self.assertEqual(45, progress["progress_percent"])
        self.assertEqual([0], progress["received_chunks"])

    def test_persona_delete_preflights_processing_imports_before_removing_anything(self) -> None:
        value = b"partial"
        self.uploads.put_chunk(self.job.id, 0, len(value), self.digest(value), io.BytesIO(value))
        processing = self.imports.create(self.job.persona_id, "processing.txt", 1, "text/plain")
        self.imports.save(replace(processing, state=ImportState.PROCESSING))

        with self.assertRaises(UploadError) as captured:
            self.uploads.delete_persona_imports(None, self.job.persona_id)

        self.assertEqual("deletion_unavailable", captured.exception.code)
        self.assertIsNotNone(self.imports.repository.get(self.job.id))
        self.assertTrue(self.uploads._chunk_path(self.job.id, 0).exists())

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

    def test_media_inspection_does_not_block_another_import_upload(self) -> None:
        content = b"media"
        media_job = self.imports.create(self.job.persona_id, "photo.png", len(content), "image/png")
        self.uploads.put_chunk(
            media_job.id, 0, len(content), self.digest(content), io.BytesIO(content)
        )
        self.uploads.complete(media_job.id, self.digest(content))
        started = threading.Event()
        release = threading.Event()
        self.uploads.media_inspector = BlockingMediaInspector(started, release)
        failures: list[BaseException] = []

        def inspect() -> None:
            try:
                self.uploads.inspect_media(None, media_job.id)
            except BaseException as exc:  # Preserve a worker failure for the assertion thread.
                failures.append(exc)

        inspection_thread = threading.Thread(target=inspect)
        inspection_thread.start()
        self.assertTrue(started.wait(timeout=1), "media inspection did not start")

        other_content = b"next"
        other_job = self.imports.create(
            self.job.persona_id, "other.txt", len(other_content), "text/plain"
        )
        uploaded = threading.Event()

        def upload_other_import() -> None:
            try:
                self.uploads.put_chunk(
                    other_job.id,
                    0,
                    len(other_content),
                    self.digest(other_content),
                    io.BytesIO(other_content),
                )
            except BaseException as exc:  # Preserve a worker failure for the assertion thread.
                failures.append(exc)
            finally:
                uploaded.set()

        upload_thread = threading.Thread(target=upload_other_import)
        upload_thread.start()
        completed_while_inspecting = uploaded.wait(timeout=0.5)
        release.set()
        inspection_thread.join(timeout=2)
        upload_thread.join(timeout=2)

        self.assertTrue(completed_while_inspecting, "media inspection held the global upload lock")
        self.assertFalse(inspection_thread.is_alive())
        self.assertFalse(upload_thread.is_alive())
        self.assertEqual([], failures)

    def test_media_inspection_rejects_insufficient_temporary_storage(self) -> None:
        content = b"media"
        job = self.imports.create(self.job.persona_id, "photo.png", len(content), "image/png")
        self.uploads.put_chunk(job.id, 0, len(content), self.digest(content), io.BytesIO(content))
        self.uploads.complete(job.id, self.digest(content))
        self.uploads.media_inspector = BlockingMediaInspector(threading.Event(), threading.Event())

        with patch(
            "src.services.upload_service.shutil.disk_usage",
            return_value=SimpleNamespace(free=0),
        ):
            with self.assertRaises(UploadError) as captured:
                self.uploads.inspect_media(None, job.id)

        self.assertEqual("media_inspection_storage_unavailable", captured.exception.code)

    def test_media_inspection_reports_a_failed_temporary_file_cleanup(self) -> None:
        content = b"media"
        job = self.imports.create(self.job.persona_id, "photo.png", len(content), "image/png")
        self.uploads.put_chunk(job.id, 0, len(content), self.digest(content), io.BytesIO(content))
        self.uploads.complete(job.id, self.digest(content))
        completed = threading.Event()
        completed.set()
        self.uploads.media_inspector = BlockingMediaInspector(completed, completed)
        original_unlink = Path.unlink

        def fail_only_media_temp_cleanup(path: Path, *args: object, **kwargs: object) -> None:
            if path.parent.name == "media-inspection":
                raise OSError("test cleanup failure")
            original_unlink(path, *args, **kwargs)

        with patch("src.services.upload_service.Path.unlink", new=fail_only_media_temp_cleanup):
            with self.assertRaises(UploadError) as captured:
                self.uploads.inspect_media(None, job.id)

        self.assertEqual("media_inspection_cleanup_failed", captured.exception.code)
        for temporary in (self.root / "media-inspection").glob("*.bin"):
            temporary.unlink()

    def test_constructor_removes_stale_media_inspection_plaintext(self) -> None:
        stale = self.uploads.storage.object_path("media-inspection", "stale", ".bin")
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_bytes(b"temporary plaintext")

        UploadService(self.uploads.storage, self.imports, self.encryption, read_block_bytes=4)

        self.assertFalse(stale.exists())

    def test_media_inspection_releases_its_payload_lease_after_a_failure(self) -> None:
        content = b"media"
        job = self.imports.create(self.job.persona_id, "photo.png", len(content), "image/png")
        self.uploads.put_chunk(job.id, 0, len(content), self.digest(content), io.BytesIO(content))
        self.uploads.complete(job.id, self.digest(content))

        def fail_inspection(_source: Path, _declared_media_type: str) -> dict[str, object]:
            raise MediaInspectionError("media_metadata_invalid", "expected test failure")

        self.uploads.media_inspector.inspect = fail_inspection
        with self.assertRaises(UploadError) as captured:
            self.uploads.inspect_media(None, job.id)

        self.assertEqual("media_metadata_invalid", captured.exception.code)
        self.assertEqual({}, self.uploads._payload_access_locks)

    def test_payload_lock_registry_does_not_keep_unknown_delete_requests(self) -> None:
        with self.assertRaises(ImportNotFoundError):
            self.uploads.delete_import(None, "not-real")

        self.assertEqual({}, self.uploads._payload_access_locks)

    def test_persona_delete_retries_when_a_child_disappears_while_acquiring_locks(self) -> None:
        other = self.imports.create(self.job.persona_id, "other.txt", 1, "text/plain")
        first_id, second_id = sorted((self.job.id, other.id))
        original_access = self.uploads._payload_access
        first_lock_reached = threading.Event()
        release_first_lock = threading.Event()
        failures: list[BaseException] = []
        result: dict[str, int] = {}

        @contextmanager
        def delay_first_access(import_id: str):
            if import_id == first_id and not first_lock_reached.is_set():
                first_lock_reached.set()
                if not release_first_lock.wait(timeout=5):
                    raise AssertionError("persona deletion test did not release the first lock")
            with original_access(import_id):
                yield

        self.uploads._payload_access = delay_first_access

        def delete_persona_imports() -> None:
            try:
                result["count"] = self.uploads.delete_persona_imports(None, self.job.persona_id)
            except BaseException as exc:  # Preserve a worker failure for the assertion thread.
                failures.append(exc)

        deletion_thread = threading.Thread(target=delete_persona_imports)
        deletion_thread.start()
        try:
            self.assertTrue(first_lock_reached.wait(timeout=1), "persona deletion did not begin lock acquisition")
            self.uploads.delete_import(None, second_id)
        finally:
            release_first_lock.set()
            deletion_thread.join(timeout=2)

        self.assertFalse(deletion_thread.is_alive())
        self.assertEqual([], failures)
        self.assertEqual(1, result["count"])
        self.assertIsNone(self.imports.repository.get(first_id))
        self.assertIsNone(self.imports.repository.get(second_id))


if __name__ == "__main__":
    unittest.main()
