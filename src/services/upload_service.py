"""Resumable chunk storage with bounded memory and explicit integrity checks."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping
from uuid import uuid4

from src.domain.messages import MessageValidationError, NormalizedMessage
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.blob_store import BlobStore, LocalBlobStore, StorageError
from src.services.import_repository import ImportRepositoryError
from src.services.import_service import ImportJob, ImportService, ImportState
from src.preprocessing.media_inspector import MediaInspectionError, MediaInspector
from src.preprocessing.parser_registry import ParserError, ParserRegistry
from src.services.plaintext_lease import PlaintextLeaseRegistry
from src.services.storage import StorageLayout


DEFAULT_CHUNK_BYTES = 8 * 1024**2
DEFAULT_READ_BLOCK_BYTES = 64 * 1024
DEFAULT_TRAINING_RECORD_BYTES = 64 * 1024
DEFAULT_PREVIEW_RECORDS = 20
MAX_PREVIEW_RECORDS = 100
PARTICIPANT_ROLES = frozenset({"persona", "user", "other", "unknown"})
MAX_PARTICIPANT_ID_CHARACTERS = 128
MAX_PARTICIPANT_MAPPINGS = 4_096
CORRECTION_STATES = frozenset({"accepted", "needs_review", "rejected"})
CORRECTION_FIELDS = frozenset({"sender_id", "sender_name", "content", "timestamp", "message_type"})
MAX_CORRECTIONS = 4_096
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


class UploadError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChunkReceipt:
    import_id: str
    index: int
    length: int
    sha256: str
    duplicate: bool
    received_bytes: int
    total_bytes: int


@dataclass(slots=True)
class _PayloadAccessEntry:
    lock: threading.RLock
    leases: int = 0


class _BlobReader:
    """Small BinaryIO adapter over bounded BlobStore blocks."""

    def __init__(self, blocks: Iterator[bytes]):
        self._blocks = iter(blocks)
        self._pending = b""
        self._exhausted = False

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        if size < 0:
            chunks = [self._pending]
            self._pending = b""
            chunks.extend(self._blocks)
            self._exhausted = True
            return b"".join(chunks)

        while not self._exhausted and len(self._pending) < size:
            try:
                block = next(self._blocks)
            except StopIteration:
                self._exhausted = True
                break
            if not isinstance(block, bytes):
                raise TypeError("BlobStore returned a non-bytes block")
            self._pending += block
        value = self._pending[:size]
        self._pending = self._pending[size:]
        return value


class UploadService:
    def __init__(
        self,
        storage: StorageLayout,
        imports: ImportService,
        encryption: AuthenticatedEncryptionService,
        max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        read_block_bytes: int = DEFAULT_READ_BLOCK_BYTES,
        parsers: ParserRegistry | None = None,
        media_inspector: MediaInspector | None = None,
        blob_store: BlobStore | None = None,
    ):
        if max_chunk_bytes <= 0 or read_block_bytes <= 0:
            raise ValueError("chunk and read block limits must be positive")
        if max_chunk_bytes > encryption.max_plaintext_bytes:
            raise ValueError("chunk limit cannot exceed the encryption segment limit")
        self.storage = storage
        self.blob_store = blob_store or LocalBlobStore(storage)
        self.imports = imports
        self.encryption = encryption
        self.max_chunk_bytes = max_chunk_bytes
        self.read_block_bytes = min(read_block_bytes, max_chunk_bytes)
        self.parsers = parsers or ParserRegistry.with_builtins()
        self.media_inspector = media_inspector or MediaInspector()
        # The development runtime is one process. The lock prevents two request
        # threads from racing the same JSON manifest; production will replace
        # this with transactional metadata storage.
        self._lock = threading.RLock()
        # Media inspection can legitimately run for seconds against a large completed
        # import. Per-import locks keep its immutable encrypted payload alive without
        # serializing unrelated uploads behind the service-wide manifest lock.
        self._payload_access_locks: dict[str, _PayloadAccessEntry] = {}
        self._cleanup_stale_media_inspection_files()
        self._stale_training_source_cleanup_failures = self._cleanup_stale_training_source_files()

    def put_chunk(
        self,
        owner_id: str,
        import_id: str,
        index: int,
        declared_length: int,
        sha256: str,
        stream: BinaryIO | None = None,
    ) -> ChunkReceipt:
        if stream is None:
            stream = sha256
            sha256 = declared_length
            declared_length = index
            index = import_id
            import_id = owner_id
            owner_id = None
        index = self._validate_index(index)
        declared_length = self._validate_length(declared_length)
        digest = self._validate_digest(sha256)

        with self._lock:
            job = self.imports.get(owner_id, import_id)
            if job.state in {
                ImportState.UPLOADED,
                ImportState.PROCESSING,
                ImportState.COMPLETED,
                ImportState.CANCELLED,
            }:
                raise UploadError("upload_closed", "the import no longer accepts chunks")

            manifest = self._load_manifest(owner_id, import_id)
            chunks = manifest["chunks"]
            existing = chunks.get(str(index))
            if existing is not None:
                existing = self._chunk_entry(existing)
                if existing["length"] != declared_length or existing["sha256"] != digest:
                    raise UploadError("chunk_conflict", "chunk index already has different content")
                actual_digest = self._consume_and_hash(stream, declared_length)
                if actual_digest != digest:
                    raise UploadError("chunk_digest_mismatch", "chunk digest does not match its body")
                return self._receipt(job, index, declared_length, digest, duplicate=True)

            received_bytes = sum(int(self._chunk_entry(item)["length"]) for item in chunks.values())
            if received_bytes + declared_length > job.total_bytes:
                raise UploadError("import_size_exceeded", "chunk exceeds the import's declared size")

            plaintext, actual_digest = self._read_and_hash(stream, declared_length)
            if actual_digest != digest:
                raise UploadError("chunk_digest_mismatch", "chunk digest does not match its body")

            encrypted = self._encrypt_segment(
                plaintext, self.chunk_aad(import_id, index, final=False), "chunk_encryption_failed"
            )
            try:
                self.blob_store.put(
                    self._chunk_key(import_id, index),
                    io.BytesIO(encrypted),
                    length=len(encrypted),
                    sha256=hashlib.sha256(encrypted).hexdigest(),
                )
            except StorageError as exc:
                if exc.code == "object_conflict":
                    raise UploadError(
                        "chunk_conflict", "chunk index already has different content"
                    ) from exc
                raise UploadError(
                    "storage_write_failed", "encrypted chunk could not be stored"
                ) from exc

            chunks[str(index)] = {
                "length": declared_length,
                "sha256": digest,
                "encrypted_length": len(encrypted),
            }
            manifest["version"] = 2
            received_bytes += declared_length
            updated = replace(
                job,
                received_bytes=received_bytes,
                chunk_count=len(chunks),
                state=ImportState.UPLOADING,
                updated_at=datetime.now(UTC).isoformat(),
            )
            try:
                self.imports.save_state(owner_id, updated, manifest)
            except ImportRepositoryError as exc:
                self.blob_store.delete(self._chunk_key(import_id, index))
                raise UploadError(
                    "metadata_persistence_failed", "import metadata could not be committed"
                ) from exc
            return self._receipt(updated, index, declared_length, digest, duplicate=False)

    def complete(
        self,
        owner_id: str,
        import_id: str | None = None,
        whole_sha256: str | None = None,
    ) -> ImportJob:
        if import_id is None or (whole_sha256 is None and isinstance(import_id, str) and _SHA256.fullmatch(import_id)):
            whole_sha256 = import_id if import_id is not None and import_id != owner_id else whole_sha256
            import_id = owner_id
            owner_id = None
        expected_digest = self._validate_digest(whole_sha256) if whole_sha256 is not None else None
        with self._lock:
            job = self.imports.get(owner_id, import_id)
            payload_key = self._payload_key(import_id)
            payload_exists = self._blob_exists(
                payload_key,
                "storage_read_failed",
                "completed payload availability could not be checked",
            )
            if job.state is ImportState.UPLOADED and payload_exists:
                return job
            if job.state in {ImportState.PROCESSING, ImportState.COMPLETED, ImportState.CANCELLED}:
                raise UploadError("upload_closed", "the import is already being processed")

            manifest = self._load_manifest(owner_id, import_id)
            chunks: Mapping[str, Mapping[str, Any]] = manifest["chunks"]
            indexes = sorted(int(value) for value in chunks)
            entries = {index: self._chunk_entry(chunks[str(index)]) for index in indexes}
            total = sum(int(entries[index]["length"]) for index in indexes)
            if total != job.total_bytes or indexes != list(range(len(indexes))):
                raise UploadError("upload_incomplete", "all bytes and contiguous chunks are required")

            digest = hashlib.sha256()
            encrypted_digest = hashlib.sha256()
            encrypted_length = 0
            with tempfile.TemporaryFile(mode="w+b") as output:
                for index in indexes:
                    part_key = self._chunk_key(import_id, index)
                    try:
                        if not self.blob_store.exists(part_key):
                            raise UploadError("chunk_missing", f"stored chunk {index} is missing")
                        entry = entries[index]
                        encrypted = self._read_blob_exact(
                            part_key,
                            self._encrypted_length(entry),
                            "chunk_corrupt",
                        )
                    except StorageError as exc:
                        if exc.code == "object_not_found":
                            raise UploadError(
                                "chunk_missing", f"stored chunk {index} is missing"
                            ) from exc
                        raise UploadError("chunk_corrupt", "stored chunk could not be read") from exc
                    try:
                        plaintext = self.encryption.decrypt(
                            encrypted, self.chunk_aad(import_id, index, final=False)
                        )
                    except (AuthenticationError, InvalidEncryptedPayloadError) as exc:
                        raise UploadError(
                            "chunk_authentication_failed", "stored chunk authentication failed"
                        ) from exc
                    if len(plaintext) != int(entry["length"]):
                        raise UploadError("chunk_corrupt", "stored chunk length is invalid")
                    if hashlib.sha256(plaintext).hexdigest() != str(entry["sha256"]).lower():
                        raise UploadError("chunk_corrupt", "stored chunk digest is invalid")
                    output.write(encrypted)
                    encrypted_digest.update(encrypted)
                    encrypted_length += len(encrypted)
                    digest.update(plaintext)
                final = self._encrypt_segment(
                    b"", self.chunk_aad(import_id, len(indexes), final=True), "payload_encryption_failed"
                )
                output.write(final)
                encrypted_digest.update(final)
                encrypted_length += len(final)
                output.flush()
                output.seek(0)
                if expected_digest is not None and digest.hexdigest() != expected_digest:
                    raise UploadError("payload_digest_mismatch", "completed payload digest does not match")
                try:
                    self.blob_store.put(
                        payload_key,
                        output,
                        length=encrypted_length,
                        sha256=encrypted_digest.hexdigest(),
                    )
                except StorageError as exc:
                    raise UploadError(
                        "storage_write_failed", "completed encrypted payload could not be stored"
                    ) from exc

            manifest["version"] = 2
            manifest["final_encrypted_length"] = len(final)
            completed = replace(
                job,
                state=ImportState.UPLOADED,
                updated_at=datetime.now(UTC).isoformat(),
            )
            try:
                self.imports.save_state(owner_id, completed, manifest)
            except ImportRepositoryError as exc:
                self.blob_store.delete(payload_key)
                raise UploadError(
                    "metadata_persistence_failed", "import metadata could not be committed"
                ) from exc
            return completed

    def cancel(self, owner_id: str, import_id: str | None = None) -> ImportJob:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        with self._lock:
            job = self.imports.get(owner_id, import_id)
            if job.state is ImportState.CANCELLED:
                return job
            if job.state in {
                ImportState.UPLOADED,
                ImportState.PROCESSING,
                ImportState.COMPLETED,
            }:
                raise UploadError("upload_closed", "the import can no longer be cancelled")

            manifest = self._load_manifest(owner_id, import_id)
            indexes = self._manifest_indexes(manifest)
            cancelled = replace(
                job,
                received_bytes=0,
                chunk_count=0,
                state=ImportState.CANCELLED,
                updated_at=datetime.now(UTC).isoformat(),
            )
            cancelled_manifest = dict(manifest)
            cancelled_manifest["version"] = 2
            cancelled_manifest["chunks"] = {}
            cancelled_manifest.pop("final_encrypted_length", None)
            self.imports.save_state(owner_id, cancelled, cancelled_manifest)

            for index in indexes:
                self._delete_blob(self._chunk_key(import_id, index), "upload chunks could not be removed")
            self._delete_blob(self._payload_key(import_id), "completed payload could not be removed")
            return cancelled

    def delete_import(
        self,
        owner_id: str,
        import_id: str | None = None,
    ) -> dict[str, Any]:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        # Acquire the import-specific lock before the global lock. A media reader uses
        # the same order, so deletion waits only for that import rather than blocking
        # all uploads while a large file is inspected.
        with self._payload_access(import_id):
            with self._lock:
                return self._delete_import_locked(owner_id, import_id)

    def delete_persona_imports(self, owner_id: str, persona_id: str) -> int:
        # A competing individual deletion can remove a child after the first list.
        # Acquire all per-import leases in a stable order, re-read under the global
        # lock, and retry before deleting anything if the set changed.
        for _ in range(3):
            with self._lock:
                jobs = self.imports.list_for_persona(owner_id, persona_id)
                self._require_persona_imports_deletable(jobs)
                job_ids = tuple(sorted(job.id for job in jobs))

            with ExitStack() as leases:
                for import_id in job_ids:
                    leases.enter_context(self._payload_access(import_id))
                with self._lock:
                    current_jobs = self.imports.list_for_persona(owner_id, persona_id)
                    self._require_persona_imports_deletable(current_jobs)
                    current_ids = tuple(sorted(job.id for job in current_jobs))
                    if current_ids != job_ids:
                        continue
                    for job in sorted(current_jobs, key=lambda item: item.id):
                        self._delete_import_locked(owner_id, job.id)
                    return len(current_jobs)
        raise UploadError(
            "deletion_unavailable",
            "persona imports changed while deletion was being prepared",
        )

    @staticmethod
    def _require_persona_imports_deletable(jobs: list[ImportJob]) -> None:
        if any(job.state is ImportState.PROCESSING for job in jobs):
            raise UploadError(
                "deletion_unavailable",
                "persona has processing imports that cannot be deleted",
            )

    def _delete_import_locked(self, owner_id: str, import_id: str) -> dict[str, Any]:
        """Delete one import while its caller holds both ordering-compatible locks."""

        job = self.imports.get(owner_id, import_id)
        if job.state is ImportState.PROCESSING:
            raise UploadError(
                "deletion_unavailable",
                "processing imports cannot be deleted",
            )
        manifest = self._load_manifest(owner_id, import_id)
        indexes = self._manifest_indexes(manifest)
        for index in indexes:
            self._delete_blob(self._chunk_key(import_id, index), "import files could not be removed")
        self._delete_blob(self._payload_key(import_id), "import files could not be removed")

        try:
            deleted = self.imports.delete(owner_id, import_id)
        except ImportRepositoryError as exc:
            raise UploadError(
                "deletion_failed",
                "import metadata could not be removed",
            ) from exc
        if not deleted:
            raise UploadError("deletion_failed", "import metadata could not be removed")
        return {
            "import_id": job.id,
            "deleted": True,
        }

    def _delete_blob(self, key: str, message: str) -> None:
        try:
            self.blob_store.delete(key)
        except StorageError as exc:
            raise UploadError("deletion_failed", message) from exc

    def _blob_exists(self, key: str, code: str, message: str) -> bool:
        try:
            return self.blob_store.exists(key)
        except StorageError as exc:
            raise UploadError(code, message) from exc

    def _materialize_blob_to_temp(self, key: str) -> Path:
        destination: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix="past-partner-preview-",
                suffix=".bin",
                delete=False,
            ) as output:
                destination = Path(output.name)
                for block in self.blob_store.iter_bytes(
                    key,
                    block_bytes=self.read_block_bytes,
                ):
                    if not isinstance(block, bytes):
                        raise TypeError("BlobStore returned a non-bytes block")
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            return destination
        except (OSError, StorageError, TypeError) as exc:
            if destination is not None:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
            raise UploadError(
                "preview_storage_unavailable",
                "preview source could not be materialized",
            ) from exc

    def missing_chunks(
        self,
        owner_id: str,
        import_id: str | None = None,
        expected_chunks: int | None = None,
    ) -> dict[str, Any]:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        expected_chunks = self._validate_expected_chunk_count(expected_chunks)
        with self._lock:
            job = self.imports.get(owner_id, import_id)
            manifest = self._load_manifest(owner_id, import_id)
            received_chunks = self._manifest_indexes(manifest)
            highest_index = received_chunks[-1] if received_chunks else -1
            if expected_chunks is None:
                expected_chunks = highest_index + 1
            if expected_chunks <= highest_index:
                raise UploadError(
                    "invalid_expected_chunk_count",
                    "expected chunk count is below a stored chunk index",
                )
            received_set = set(received_chunks)
            return {
                "import_id": job.id,
                "state": job.state.value,
                "total_bytes": job.total_bytes,
                "received_bytes": job.received_bytes,
                "chunk_count": len(received_chunks),
                "expected_chunk_count": expected_chunks,
                "received_chunks": received_chunks,
                "missing_chunks": [
                    index for index in range(expected_chunks) if index not in received_set
                ],
            }

    def progress(
        self,
        owner_id: str,
        import_id: str | None = None,
    ) -> dict[str, Any]:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        with self._lock:
            job = self.imports.get(owner_id, import_id)
            manifest = self._load_manifest(owner_id, import_id)
            received_chunks = self._manifest_indexes(manifest)
            if job.total_bytes == 0:
                progress_percent = 100 if job.state is ImportState.UPLOADED else 0
            else:
                progress_percent = min(100, (job.received_bytes * 100) // job.total_bytes)
            return {
                "import_id": job.id,
                "state": job.state.value,
                "total_bytes": job.total_bytes,
                "received_bytes": job.received_bytes,
                "progress_percent": progress_percent,
                "chunk_count": len(received_chunks),
                "received_chunks": received_chunks,
            }

    def preview(
        self,
        owner_id: str,
        import_id: str | None = None,
        max_records: int = DEFAULT_PREVIEW_RECORDS,
    ) -> dict[str, Any]:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        if isinstance(max_records, bool) or not isinstance(max_records, int):
            raise UploadError("invalid_preview_limit", "preview limit must be an integer")
        if max_records <= 0 or max_records > MAX_PREVIEW_RECORDS:
            raise UploadError(
                "invalid_preview_limit",
                f"preview limit must be between 1 and {MAX_PREVIEW_RECORDS}",
            )

        with self._lock:
            job = self.imports.get(owner_id, import_id)
            if job.state is not ImportState.UPLOADED or not self._blob_exists(
                self._payload_key(import_id),
                "storage_read_failed",
                "completed payload availability could not be checked",
            ):
                raise UploadError(
                    "preview_unavailable",
                    "preview requires a completed uploaded import",
                )
            file_specs = [
                {
                    "file_id": item.file_id if len(job.files) > 1 else None,
                    "source_name": item.source_name,
                    "media_type": item.media_type,
                    "total_bytes": item.total_bytes,
                }
                for item in job.files
            ]
            if not file_specs:
                file_specs = [
                    {
                        "file_id": None,
                        "source_name": job.source_name,
                        "media_type": job.media_type,
                        "total_bytes": job.total_bytes,
                    }
                ]
            multi_file = len(file_specs) > 1
            source_name = file_specs[0]["source_name"]
            media_type = file_specs[0]["media_type"]
            manifest = self._load_manifest(owner_id, import_id)
            participant_mapping = _normalize_participant_mapping(manifest.get("participant_mapping"))
            corrections = _normalize_corrections(manifest.get("corrections"))
            preview_id = uuid4().hex
            payload = iter(self.iter_payload(owner_id, import_id))
            pending = b""
            records: list[dict[str, Any]] = []
            file_summaries: list[dict[str, Any]] = []
            warnings: list[str] = []
            source_types: list[str] = []
            confidences: list[float] = []
            truncated = False
            remaining_records = max_records
            try:
                for index, file_spec in enumerate(file_specs):
                    preview_key = f"preview/{preview_id}-{index}.bin"
                    destination: Path | None = None
                    try:
                        remaining_bytes = int(file_spec["total_bytes"])
                        source_digest = hashlib.sha256()
                        source_length = 0
                        with tempfile.TemporaryFile(mode="w+b") as source:
                            while remaining_bytes:
                                if not pending:
                                    try:
                                        pending = next(payload)
                                    except StopIteration as exc:
                                        raise UploadError(
                                            "payload_corrupt",
                                            "payload ended before the manifest file boundary",
                                        ) from exc
                                take = min(remaining_bytes, len(pending))
                                block = pending[:take]
                                source.write(block)
                                source_digest.update(block)
                                source_length += len(block)
                                pending = pending[take:]
                                remaining_bytes -= take
                            source.flush()
                            source.seek(0)
                            try:
                                self.blob_store.put(
                                    preview_key,
                                    source,
                                    length=source_length,
                                    sha256=source_digest.hexdigest(),
                                )
                            except StorageError as exc:
                                raise UploadError(
                                    "preview_storage_unavailable",
                                    "preview source could not be stored",
                                ) from exc

                        destination = self._materialize_blob_to_temp(preview_key)

                        namespace = job.id
                        if multi_file:
                            namespace = f"{job.id}:{file_spec['file_id']}"
                        parse_limit = max(1, remaining_records)
                        try:
                            result = self.parsers.parse(
                                destination,
                                {
                                    "source_name": file_spec["source_name"],
                                    "media_type": file_spec["media_type"],
                                    "record_id_namespace": namespace,
                                },
                                max_records=parse_limit,
                            )
                        except ParserError as exc:
                            raise UploadError(
                                exc.code,
                                f"{file_spec['source_name']}: {exc}",
                            ) from exc

                        source_types.append(result.source_type)
                        summary = dict(result.summary)
                        returned_records = result.records
                        if remaining_records <= 0:
                            # Continue probing later files after the global limit is
                            # reached, but never append records beyond that limit.
                            returned_records = ()
                            summary["record_count"] = 0
                            summary["truncated"] = True
                        file_warnings = list(result.warnings)
                        warnings.extend(file_warnings)
                        confidences.append(float(summary.get("confidence", 0.0)))
                        truncated = truncated or bool(summary.get("truncated"))
                        file_summaries.append(
                            {
                                **file_spec,
                                "source_type": result.source_type,
                                "summary": summary,
                                "warnings": file_warnings,
                            }
                        )
                        remaining_records -= len(returned_records)
                        for record_index, record in enumerate(returned_records):
                            record_id = record.record_id or _record_id(
                                namespace, result.source_type, record_index
                            )
                            values = record.to_dict()
                            correction = corrections.get(record_id)
                            review_state = "needs_review"
                            if correction is not None:
                                values.update(correction["fields"])
                                review_state = correction["review_state"]
                                try:
                                    values = NormalizedMessage.from_mapping(values).to_dict()
                                except MessageValidationError as exc:
                                    raise UploadError(
                                        "correction_corrupt",
                                        f"correction for record {record_id} is invalid",
                                    ) from exc
                            preview_record = _preview_record(values)
                            preview_record["record_id"] = record_id
                            preview_record["review_state"] = review_state
                            preview_record["sender_role"] = participant_mapping.get(
                                preview_record["sender_id"], "unknown"
                            )
                            if multi_file:
                                preview_record["file_id"] = file_spec["file_id"]
                                preview_record["source_name"] = file_spec["source_name"]
                                preview_record["media_type"] = file_spec["media_type"]
                                preview_record["source_type"] = result.source_type
                            records.append(preview_record)
                    finally:
                        if destination is not None:
                            try:
                                destination.unlink(missing_ok=True)
                            except OSError as exc:
                                raise UploadError(
                                    "preview_cleanup_failed",
                                    "preview temporary data could not be removed",
                                ) from exc
                        self._delete_blob(
                            preview_key,
                            "preview temporary object could not be removed",
                        )

                if pending or next(payload, None) is not None:
                    raise UploadError(
                        "payload_corrupt",
                        "payload contains bytes outside the manifest file boundaries",
                    )

                unique_source_types = set(source_types)
                source_type = next(iter(unique_source_types), "unknown")
                if len(unique_source_types) > 1:
                    source_type = "mixed"
                if len(file_specs) == 1:
                    summary = dict(file_summaries[0]["summary"])
                else:
                    summary = {
                        "record_count": len(records),
                        "warning_count": len(warnings),
                        "unsupported_count": sum(
                            int(item["summary"].get("unsupported_count", 0))
                            for item in file_summaries
                        ),
                        "confidence": min(confidences) if confidences else 0.0,
                        "truncated": truncated,
                        "file_count": len(file_specs),
                    }
                return {
                    "import_id": job.id,
                    "state": job.state.value,
                    "source_name": source_name,
                    "media_type": media_type,
                    "source_type": source_type,
                    "summary": summary,
                    "warnings": warnings,
                    "records": records,
                    "file_summaries": file_summaries,
                }
            finally:
                pending = b""

    def iter_training_records(
        self,
        owner_id: str,
        import_id: str,
        *,
        max_record_bytes: int = DEFAULT_TRAINING_RECORD_BYTES,
    ) -> Iterator[dict[str, str]]:
        """Yield corrected, mapped records without buffering a completed import.

        A training dataset is allowed to read only a completed import belonging to a
        caller already scoped by the service layer. The per-import lease keeps the
        encrypted payload from being deleted while each parser consumes one temporary
        source file, while unrelated uploads can still acquire the global manifest
        lock between individual operations.
        """

        self._require_stale_training_source_cleanup()
        if (
            isinstance(max_record_bytes, bool)
            or not isinstance(max_record_bytes, int)
            or max_record_bytes <= 0
            or max_record_bytes > DEFAULT_TRAINING_RECORD_BYTES
        ):
            raise UploadError(
                "invalid_training_limit",
                "training record byte limit must be a positive value within the safe maximum",
            )

        def iterator() -> Iterator[dict[str, str]]:
            with self._payload_access(import_id):
                with self._lock:
                    job = self.imports.get(owner_id, import_id)
                    if job.state is not ImportState.UPLOADED or not self._blob_exists(
                        self._payload_key(import_id),
                        "storage_read_failed",
                        "completed payload availability could not be checked",
                    ):
                        raise UploadError(
                            "training_dataset_unavailable",
                            "training data requires a completed uploaded import",
                        )
                    file_specs = [
                        {
                            "file_id": item.file_id if len(job.files) > 1 else None,
                            "source_name": item.source_name,
                            "media_type": item.media_type,
                            "total_bytes": item.total_bytes,
                        }
                        for item in job.files
                    ]
                    if not file_specs:
                        file_specs = [
                            {
                                "file_id": None,
                                "source_name": job.source_name,
                                "media_type": job.media_type,
                                "total_bytes": job.total_bytes,
                            }
                        ]
                    manifest = self._load_manifest(owner_id, import_id)
                    participant_mapping = _normalize_participant_mapping(
                        manifest.get("participant_mapping")
                    )
                    corrections = _normalize_corrections(manifest.get("corrections"))

                # Missing mappings must fail before any encrypted payload is opened:
                # unknown authors can never be upgraded to a training target by a
                # fallback role or by the dataset builder.
                if not participant_mapping:
                    raise UploadError(
                        "training_mapping_unavailable",
                        "training data requires an explicit participant mapping",
                    )
                if "persona" not in participant_mapping.values():
                    raise UploadError(
                        "training_persona_mapping_unavailable",
                        "training data requires an explicit target persona mapping",
                    )

                training_id = uuid4().hex
                payload = iter(
                    self._iter_completed_payload_while_leased(
                        owner_id,
                        import_id,
                        unavailable_code="training_dataset_unavailable",
                        unavailable_message="completed encrypted payload is unavailable for training",
                    )
                )
                pending = b""
                try:
                    for index, file_spec in enumerate(file_specs):
                        destination = self.storage.object_path(
                            "training-source", f"{training_id}-{index}", ".bin"
                        )
                        remaining_bytes = int(file_spec["total_bytes"])
                        self._prepare_training_source_destination(destination, remaining_bytes)
                        try:
                            try:
                                with destination.open("xb") as output:
                                    while remaining_bytes:
                                        if not pending:
                                            try:
                                                pending = next(payload)
                                            except StopIteration as exc:
                                                raise UploadError(
                                                    "payload_corrupt",
                                                    "payload ended before the manifest file boundary",
                                                ) from exc
                                        take = min(remaining_bytes, len(pending))
                                        output.write(pending[:take])
                                        pending = pending[take:]
                                        remaining_bytes -= take
                                    output.flush()
                                    os.fsync(output.fileno())
                            except OSError as exc:
                                raise UploadError(
                                    "training_dataset_storage_unavailable",
                                    "temporary training source storage is unavailable",
                                ) from exc

                            namespace = job.id
                            if len(file_specs) > 1:
                                namespace = f"{job.id}:{file_spec['file_id']}"
                            try:
                                for record in self.parsers.iter_records(
                                    destination,
                                    {
                                        "source_name": file_spec["source_name"],
                                        "media_type": file_spec["media_type"],
                                        "record_id_namespace": namespace,
                                        "max_record_bytes": max_record_bytes,
                                    },
                                ):
                                    record_id = record.record_id
                                    if record_id is None:
                                        raise UploadError(
                                            "training_dataset_invalid",
                                            "parser record is missing a stable identifier",
                                        )
                                    # Corrections may improve imported display fields, but
                                    # the immutable parsed sender is the only identity that
                                    # can be matched to the user-confirmed participant map.
                                    # Otherwise a user message could be relabelled as persona
                                    # text solely by editing its sender_id correction.
                                    mapped_sender_id = record.sender_id
                                    values = record.to_dict()
                                    correction = corrections.get(record_id)
                                    review_state = "needs_review"
                                    if correction is not None:
                                        values.update(correction["fields"])
                                        review_state = correction["review_state"]
                                        try:
                                            values = NormalizedMessage.from_mapping(values).to_dict()
                                        except MessageValidationError as exc:
                                            raise UploadError(
                                                "correction_corrupt",
                                                "a stored correction is not a valid message",
                                            ) from exc
                                    sender_id = values["sender_id"]
                                    content = values["content"]
                                    if not isinstance(sender_id, str) or not isinstance(content, str):
                                        raise UploadError(
                                            "training_dataset_invalid",
                                            "normalized training record fields are invalid",
                                        )
                                    yield {
                                        "record_id": record_id,
                                        "sender_role": participant_mapping.get(
                                            mapped_sender_id, "unknown"
                                        ),
                                        "review_state": review_state,
                                        "content": content,
                                    }
                            except ParserError as exc:
                                raise UploadError(
                                    exc.code,
                                    f"a completed import could not be parsed for training: {exc}",
                                ) from exc
                        finally:
                            self._remove_training_source_destination(destination)

                    if pending or next(payload, None) is not None:
                        raise UploadError(
                            "payload_corrupt",
                            "payload contains bytes outside the manifest file boundaries",
                        )
                finally:
                    pending = b""
                    payload.close()

        return iterator()

    def inspect_media(self, owner_id: str, import_id: str) -> dict[str, Any]:
        with self._lock:
            job = self.imports.get(owner_id, import_id)
            if job.state is not ImportState.UPLOADED or not self._blob_exists(
                self._payload_key(import_id),
                "storage_read_failed",
                "completed payload availability could not be checked",
            ):
                raise UploadError(
                    "media_inspection_unavailable",
                    "inspection requires a completed uploaded import",
                )
            file_specs = [
                {
                    "file_id": item.file_id if len(job.files) > 1 else None,
                    "source_name": item.source_name,
                    "media_type": item.media_type,
                    "total_bytes": item.total_bytes,
                }
                for item in job.files
            ]
            if not file_specs:
                file_specs = [
                    {
                        "file_id": None,
                        "source_name": job.source_name,
                        "media_type": job.media_type,
                        "total_bytes": job.total_bytes,
                    }
                ]

        inspection_id = uuid4().hex
        payload = iter(self._iter_media_payload(owner_id, import_id))
        pending = b""
        files: list[dict[str, Any]] = []
        try:
            for index, file_spec in enumerate(file_specs):
                destination = self.storage.object_path(
                    "media-inspection", f"{inspection_id}-{index}", ".bin"
                )
                remaining_bytes = int(file_spec["total_bytes"])
                self._prepare_media_inspection_destination(destination, remaining_bytes)
                try:
                    # ffprobe and Pillow need a seekable file. Materialize only the
                    # current manifest boundary, never a client-controlled path, then
                    # remove the plaintext before this request can return.
                    try:
                        with destination.open("xb") as output:
                            while remaining_bytes:
                                if not pending:
                                    try:
                                        pending = next(payload)
                                    except StopIteration as exc:
                                        raise UploadError(
                                            "payload_corrupt",
                                            "payload ended before the manifest file boundary",
                                        ) from exc
                                take = min(remaining_bytes, len(pending))
                                output.write(pending[:take])
                                pending = pending[take:]
                                remaining_bytes -= take
                            output.flush()
                            os.fsync(output.fileno())
                    except OSError as exc:
                        raise UploadError(
                            "media_inspection_storage_unavailable",
                            "temporary media inspection storage is unavailable",
                        ) from exc
                    try:
                        inspection = self.media_inspector.inspect(
                            destination, str(file_spec["media_type"])
                        )
                    except MediaInspectionError as exc:
                        raise UploadError(exc.code, str(exc)) from exc
                    files.append(
                        {
                            "file_id": file_spec["file_id"],
                            "source_name": file_spec["source_name"],
                            "declared_media_type": file_spec["media_type"],
                            **inspection,
                        }
                    )
                finally:
                    self._remove_media_inspection_destination(destination)

            if pending or next(payload, None) is not None:
                raise UploadError(
                    "payload_corrupt",
                    "payload contains bytes outside the manifest file boundaries",
                )
            return {
                "import_id": job.id,
                "state": job.state.value,
                "processing_mode": "local_metadata",
                "provider_transfer": False,
                "files": files,
            }
        finally:
            pending = b""
            payload.close()

    def _iter_media_payload(self, owner_id: str, import_id: str) -> Iterator[bytes]:
        """Yield an immutable completed payload without holding the service-wide lock."""

        return self._iter_completed_payload(
            owner_id,
            import_id,
            unavailable_code="media_inspection_unavailable",
            unavailable_message="completed encrypted payload is unavailable",
        )

    def _iter_completed_payload(
        self,
        owner_id: str,
        import_id: str,
        *,
        unavailable_code: str,
        unavailable_message: str,
    ) -> Iterator[bytes]:
        """Lease one completed import while callers stream decrypted chunks."""

        def iterator() -> Iterator[bytes]:
            # The per-import lock prevents deletion of an open Windows file while the
            # global lock is released for decryption and temporary-file I/O.
            with self._payload_access(import_id):
                yield from self._iter_completed_payload_while_leased(
                    owner_id,
                    import_id,
                    unavailable_code=unavailable_code,
                    unavailable_message=unavailable_message,
                )

        return iterator()

    def _iter_completed_payload_while_leased(
        self,
        owner_id: str,
        import_id: str,
        *,
        unavailable_code: str,
        unavailable_message: str,
    ) -> Iterator[bytes]:
        """Read a verified payload after the caller has acquired its payload lease."""

        with self._lock:
            job = self.imports.get(owner_id, import_id)
            payload_key = self._payload_key(import_id)
            if job.state is not ImportState.UPLOADED or not self._blob_exists(
                payload_key,
                unavailable_code,
                unavailable_message,
            ):
                raise UploadError(unavailable_code, unavailable_message)
            manifest = self._load_manifest(owner_id, import_id)
            chunks: Mapping[str, Mapping[str, Any]] = manifest["chunks"]
            indexes = sorted(int(value) for value in chunks)
            entries = {index: self._chunk_entry(chunks[str(index)]) for index in indexes}
            if indexes != list(range(len(indexes))):
                raise UploadError("manifest_corrupt", "encrypted chunk indexes are not contiguous")
            final_length = self._encrypted_length_value(manifest.get("final_encrypted_length"))

        try:
            source = _BlobReader(
                self.blob_store.iter_bytes(payload_key, block_bytes=self.read_block_bytes)
            )
            for index in indexes:
                entry = entries[index]
                encrypted = self._read_exact(
                    source, self._encrypted_length(entry), "payload_corrupt"
                )
                try:
                    plaintext = self.encryption.decrypt(
                        encrypted, self.chunk_aad(import_id, index, final=False)
                    )
                except (AuthenticationError, InvalidEncryptedPayloadError) as exc:
                    raise UploadError(
                        "payload_authentication_failed",
                        "payload chunk authentication failed",
                    ) from exc
                if len(plaintext) != int(entry["length"]):
                    raise UploadError("payload_corrupt", "payload chunk length is invalid")
                if hashlib.sha256(plaintext).hexdigest() != str(entry["sha256"]).lower():
                    raise UploadError("payload_corrupt", "payload chunk digest is invalid")
                yield plaintext

            final = self._read_exact(source, final_length, "payload_corrupt")
            try:
                sentinel = self.encryption.decrypt(
                    final, self.chunk_aad(import_id, len(indexes), final=True)
                )
            except (AuthenticationError, InvalidEncryptedPayloadError) as exc:
                raise UploadError(
                    "payload_authentication_failed",
                    "payload end marker authentication failed",
                ) from exc
            if sentinel:
                raise UploadError("payload_corrupt", "payload end marker is not empty")
            if source.read(1):
                raise UploadError("payload_corrupt", "payload has trailing bytes")
        except (OSError, StorageError, TypeError) as exc:
            raise UploadError(unavailable_code, unavailable_message) from exc

    @contextmanager
    def _payload_access(self, import_id: str) -> Iterator[None]:
        with self._lock:
            entry = self._payload_access_locks.get(import_id)
            if entry is None:
                entry = _PayloadAccessEntry(lock=threading.RLock())
                self._payload_access_locks[import_id] = entry
            entry.leases += 1
        entry.lock.acquire()
        try:
            yield
        finally:
            entry.lock.release()
            with self._lock:
                entry.leases -= 1
                if entry.leases == 0 and self._payload_access_locks.get(import_id) is entry:
                    del self._payload_access_locks[import_id]

    def _prepare_media_inspection_destination(self, destination: Path, required_bytes: int) -> None:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(destination.parent).free < required_bytes:
                raise UploadError(
                    "media_inspection_storage_unavailable",
                    "temporary media inspection storage is insufficient",
                )
        except OSError as exc:
            raise UploadError(
                "media_inspection_storage_unavailable",
                "temporary media inspection storage is unavailable",
            ) from exc

    @staticmethod
    def _remove_media_inspection_destination(destination: Path) -> None:
        try:
            destination.unlink(missing_ok=True)
        except OSError as exc:
            raise UploadError(
                "media_inspection_cleanup_failed",
                "temporary media inspection data could not be removed",
            ) from exc

    def _prepare_training_source_destination(self, destination: Path, required_bytes: int) -> None:
        PlaintextLeaseRegistry.reserve(destination)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(destination.parent).free < required_bytes:
                raise UploadError(
                    "training_dataset_storage_unavailable",
                    "temporary training source storage is insufficient",
                )
        except OSError as exc:
            PlaintextLeaseRegistry.abandon(destination)
            raise UploadError(
                "training_dataset_storage_unavailable",
                "temporary training source storage is unavailable",
            ) from exc
        except UploadError:
            PlaintextLeaseRegistry.abandon(destination)
            raise

    @staticmethod
    def _remove_training_source_destination(destination: Path) -> None:
        try:
            PlaintextLeaseRegistry.delete_and_release(destination)
        except OSError as exc:
            raise UploadError(
                "training_dataset_cleanup_failed",
                "temporary training source data could not be removed",
            ) from exc

    def _cleanup_stale_media_inspection_files(self) -> None:
        directory = self.storage.object_path("media-inspection", "sentinel").parent
        if not directory.is_dir():
            return
        for candidate in directory.glob("*.bin"):
            try:
                candidate.unlink()
            except OSError:
                # A locked stale file is retried by the next service startup instead of
                # preventing a local development server from becoming available.
                continue

    def _require_stale_training_source_cleanup(self) -> None:
        if not self._stale_training_source_cleanup_failures:
            return
        self._stale_training_source_cleanup_failures = self._cleanup_stale_training_source_files()
        if self._stale_training_source_cleanup_failures:
            raise UploadError(
                "training_dataset_cleanup_failed",
                "stale plaintext training source data could not be removed",
            )

    def _cleanup_stale_training_source_files(self) -> tuple[Path, ...]:
        directory = self.storage.object_path("training-source", "sentinel").parent
        if not directory.is_dir():
            return ()
        failures: list[Path] = []
        for candidate in directory.glob("*.bin"):
            try:
                PlaintextLeaseRegistry.delete_if_stale(candidate)
            except OSError:
                # Training reads retry this cleanup before decrypting another import;
                # a known plaintext leftover is a privacy failure, not background
                # noise that may be ignored while a provider handoff proceeds.
                failures.append(candidate)
        return tuple(failures)

    def save_corrections(
        self,
        owner_id: str,
        import_id: str | list[Mapping[str, Any]] | None = None,
        corrections: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if corrections is None and isinstance(import_id, list):
            corrections = import_id
            import_id = owner_id
            owner_id = None
        if import_id is None:
            import_id = owner_id
            owner_id = None
        normalized = _normalize_corrections(corrections, require_non_empty=True)

        with self._lock:
            job = self.imports.get(owner_id, import_id)
            if job.state is not ImportState.UPLOADED or not self._blob_exists(
                self._payload_key(import_id),
                "storage_read_failed",
                "completed payload availability could not be checked",
            ):
                raise UploadError(
                    "correction_unavailable",
                    "corrections require a completed uploaded import",
                )
            manifest = self._load_manifest(owner_id, import_id)
            existing = _normalize_corrections(manifest.get("corrections"))
            existing.update(normalized)
            manifest["corrections"] = existing
            manifest["corrections_version"] = 1
            manifest["corrections_updated_at"] = datetime.now(UTC).isoformat()
            try:
                self.imports.save_state(owner_id, job, manifest)
            except ImportRepositoryError as exc:
                raise UploadError(
                    "metadata_persistence_failed",
                    "corrections could not be committed",
                ) from exc
            return {
                "import_id": job.id,
                "state": job.state.value,
                "correction_count": len(existing),
                "updated_records": list(normalized),
            }

    def set_participant_mapping(
        self,
        owner_id: str,
        import_id: str | Mapping[str, str] | None = None,
        mapping: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Persist source participant IDs and their semantic roles in the encrypted manifest."""

        if mapping is None and isinstance(import_id, Mapping):
            mapping = import_id
            import_id = owner_id
            owner_id = None
        if import_id is None:
            import_id = owner_id
            owner_id = None
        normalized = _normalize_participant_mapping(mapping, require_non_empty=True)

        with self._lock:
            job = self.imports.get(owner_id, import_id)
            if job.state is not ImportState.UPLOADED or not self._blob_exists(
                self._payload_key(import_id),
                "storage_read_failed",
                "completed payload availability could not be checked",
            ):
                raise UploadError(
                    "mapping_unavailable",
                    "participant mapping requires a completed uploaded import",
                )
            manifest = self._load_manifest(owner_id, import_id)
            manifest["participant_mapping"] = normalized
            manifest["participant_mapping_version"] = 1
            manifest["participant_mapping_updated_at"] = datetime.now(UTC).isoformat()
            try:
                self.imports.save_state(owner_id, job, manifest)
            except ImportRepositoryError as exc:
                raise UploadError(
                    "metadata_persistence_failed",
                    "participant mapping could not be committed",
                ) from exc
            return self._participant_mapping_result(job, normalized)

    def participant_mapping(
        self,
        owner_id: str,
        import_id: str | None = None,
    ) -> dict[str, Any]:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        with self._lock:
            job = self.imports.get(owner_id, import_id)
            manifest = self._load_manifest(owner_id, import_id)
            normalized = _normalize_participant_mapping(manifest.get("participant_mapping"))
            return self._participant_mapping_result(job, normalized)

    @staticmethod
    def _participant_mapping_result(
        job: ImportJob,
        mapping: Mapping[str, str],
    ) -> dict[str, Any]:
        return {
            "import_id": job.id,
            "state": job.state.value,
            "participant_mapping": dict(mapping),
            "mapped": bool(mapping),
        }

    @staticmethod
    def chunk_aad(import_id: str, index: int, *, final: bool) -> bytes:
        marker = "true" if final else "false"
        return f"past-partner/import/{import_id}/chunk/{index}/final/{marker}".encode("ascii")

    def iter_payload(self, owner_id: str, import_id: str | None = None) -> Iterator[bytes]:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        return self._iter_completed_payload(
            owner_id,
            import_id,
            unavailable_code="payload_unavailable",
            unavailable_message="completed encrypted payload is unavailable",
        )

    @staticmethod
    def _chunk_key(import_id: str, index: int) -> str:
        return f"upload-parts/{import_id}-{index}.part"

    @staticmethod
    def _payload_key(import_id: str) -> str:
        return f"payloads/{import_id}.bin"

    def _load_manifest(self, owner_id: str, import_id: str) -> dict[str, Any]:
        value = self.imports.get_manifest(owner_id, import_id)
        if value is None:
            return {"version": 2, "import_id": import_id, "chunks": {}}
        if not isinstance(value, dict) or not isinstance(value.get("chunks"), dict):
            raise UploadError("manifest_corrupt", "upload manifest is invalid")
        if value.get("version") != 2:
            raise UploadError("manifest_version_unsupported", "encrypted upload manifest version is unsupported")
        return value

    def _manifest_indexes(self, manifest: Mapping[str, Any]) -> list[int]:
        chunks = manifest["chunks"]
        indexes: list[int] = []
        for key, value in chunks.items():
            if not isinstance(key, str) or not key.isdecimal():
                raise UploadError("manifest_corrupt", "encrypted chunk index is invalid")
            index = int(key)
            if str(index) != key or index > 1_000_000:
                raise UploadError("manifest_corrupt", "encrypted chunk index is invalid")
            self._chunk_entry(value)
            indexes.append(index)
        return sorted(indexes)

    def _read_and_hash(self, stream: BinaryIO, length: int) -> tuple[bytes, str]:
        digest = hashlib.sha256()
        plaintext = bytearray()
        remaining = length
        while remaining:
            block = stream.read(min(self.read_block_bytes, remaining))
            if not block:
                raise UploadError("chunk_length_mismatch", "chunk ended before its declared length")
            if len(block) > remaining:
                raise UploadError("chunk_length_mismatch", "chunk exceeded its declared length")
            plaintext.extend(block)
            digest.update(block)
            remaining -= len(block)
        return bytes(plaintext), digest.hexdigest()

    def _encrypt_segment(self, plaintext: bytes, aad: bytes, code: str) -> bytes:
        try:
            return self.encryption.encrypt(plaintext, aad)
        except ValueError as exc:
            raise UploadError(code, "payload segment exceeds the encryption limit") from exc

    def _encrypted_length(self, entry: Mapping[str, Any]) -> int:
        return self._encrypted_length_value(entry.get("encrypted_length"))

    def _chunk_entry(self, value: object) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise UploadError("manifest_corrupt", "encrypted chunk metadata is invalid")
        length = value.get("length")
        digest = value.get("sha256")
        if isinstance(length, bool) or not isinstance(length, int) or length <= 0:
            raise UploadError("manifest_corrupt", "encrypted chunk length is invalid")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise UploadError("manifest_corrupt", "encrypted chunk digest is invalid")
        self._encrypted_length(value)
        return value

    @staticmethod
    def _encrypted_length_value(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise UploadError("manifest_corrupt", "encrypted segment length is invalid")
        return value

    def _read_exact(self, source: BinaryIO, length: int, code: str) -> bytes:
        remaining = length
        blocks: list[bytes] = []
        while remaining:
            block = source.read(min(self.read_block_bytes, remaining))
            if not block:
                raise UploadError(code, "encrypted segment ended before its declared length")
            if len(block) > remaining:
                raise UploadError(code, "encrypted segment exceeded its declared length")
            blocks.append(block)
            remaining -= len(block)
        return b"".join(blocks)

    def _read_blob_exact(self, key: str, length: int, code: str) -> bytes:
        if length < 0:
            raise UploadError(code, "encrypted object length is invalid")
        blocks: list[bytes] = []
        actual_length = 0
        for block in self.blob_store.iter_bytes(key, block_bytes=self.read_block_bytes):
            if not isinstance(block, bytes):
                raise UploadError(code, "stored object returned invalid bytes")
            actual_length += len(block)
            if actual_length > length:
                raise UploadError(code, "stored object has trailing bytes")
            blocks.append(block)
        if actual_length != length:
            raise UploadError(code, "stored object is shorter than its manifest length")
        return b"".join(blocks)

    def _consume_and_hash(self, stream: BinaryIO, length: int) -> str:
        digest = hashlib.sha256()
        remaining = length
        while remaining:
            block = stream.read(min(self.read_block_bytes, remaining))
            if not block:
                raise UploadError("chunk_length_mismatch", "chunk ended before its declared length")
            if len(block) > remaining:
                raise UploadError("chunk_length_mismatch", "chunk exceeded its declared length")
            digest.update(block)
            remaining -= len(block)
        return digest.hexdigest()

    @staticmethod
    def _validate_index(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1_000_000:
            raise UploadError("invalid_chunk_index", "chunk index must be a bounded non-negative integer")
        return value

    @staticmethod
    def _validate_expected_chunk_count(value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 1_000_001:
            raise UploadError(
                "invalid_expected_chunk_count",
                "expected chunk count must be a bounded non-negative integer",
            )
        return value

    def _validate_length(self, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise UploadError("invalid_chunk_length", "chunk length must be a positive integer")
        if value > self.max_chunk_bytes:
            raise UploadError("chunk_too_large", "chunk exceeds the configured chunk limit")
        return value

    @staticmethod
    def _validate_digest(value: object) -> str:
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            raise UploadError("invalid_digest", "SHA-256 must be 64 hexadecimal characters")
        return value.lower()

    @staticmethod
    def _receipt(
        job: ImportJob,
        index: int,
        length: int,
        digest: str,
        duplicate: bool,
    ) -> ChunkReceipt:
        return ChunkReceipt(
            import_id=job.id,
            index=index,
            length=length,
            sha256=digest,
            duplicate=duplicate,
            received_bytes=job.received_bytes,
            total_bytes=job.total_bytes,
        )


def _preview_record(record: dict[str, Any], max_content_characters: int = 2_000) -> dict[str, Any]:
    content = record.get("content")
    if isinstance(content, str) and len(content) > max_content_characters:
        record["content"] = content[:max_content_characters]
        record["content_truncated"] = True
    return record


def _record_id(import_id: str, source_type: str, index: int) -> str:
    return hashlib.sha256(f"{import_id}:{source_type}:{index}".encode("utf-8")).hexdigest()


def _normalize_corrections(
    value: object,
    *,
    require_non_empty: bool = False,
) -> dict[str, dict[str, Any]]:
    if value is None:
        if require_non_empty:
            raise UploadError("invalid_correction", "corrections must not be empty")
        return {}
    if isinstance(value, Mapping):
        entries: list[Mapping[str, Any]] = []
        for record_id, item in value.items():
            if not isinstance(item, Mapping):
                raise UploadError("invalid_correction", "each stored correction must be an object")
            entries.append({"record_id": record_id, **dict(item)})
    elif isinstance(value, list):
        entries = value
    else:
        raise UploadError("invalid_correction", "corrections must be a list")
    if require_non_empty and not entries:
        raise UploadError("invalid_correction", "corrections must not be empty")
    if len(entries) > MAX_CORRECTIONS:
        raise UploadError(
            "invalid_correction",
            f"corrections cannot contain more than {MAX_CORRECTIONS} records",
        )

    normalized: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise UploadError("invalid_correction", "each correction must be an object")
        record_id = entry.get("record_id")
        if not isinstance(record_id, str) or not _SHA256.fullmatch(record_id):
            raise UploadError("invalid_correction", "record_id must be a 64-character hexadecimal ID")
        record_id = record_id.lower()
        fields = entry.get("fields", {})
        if not isinstance(fields, Mapping):
            raise UploadError("invalid_correction", "correction fields must be an object")
        if set(fields) - CORRECTION_FIELDS:
            raise UploadError("invalid_correction", "correction contains an unsupported field")
        normalized_fields: dict[str, str] = {}
        for field, field_value in fields.items():
            if not isinstance(field_value, str):
                raise UploadError("invalid_correction", "correction fields must be strings")
            invalid_control = (
                any(ord(character) < 32 and character not in "\r\n\t" for character in field_value)
                if field == "content"
                else any(not character.isprintable() for character in field_value)
            )
            if len(field_value) > 10_000 or invalid_control:
                raise UploadError("invalid_correction", "correction fields contain invalid text")
            normalized_fields[field] = field_value
        review_state = entry.get("review_state")
        if not isinstance(review_state, str) or review_state not in CORRECTION_STATES:
            raise UploadError(
                "invalid_correction",
                "review_state must be accepted, needs_review, or rejected",
            )
        normalized[record_id] = {
            "fields": normalized_fields,
            "review_state": review_state,
        }
    return normalized


def _normalize_participant_mapping(
    value: object,
    *,
    require_non_empty: bool = False,
) -> dict[str, str]:
    if value is None:
        if require_non_empty:
            raise UploadError("invalid_participant_mapping", "mapping must not be empty")
        return {}
    if not isinstance(value, Mapping):
        raise UploadError("invalid_participant_mapping", "mapping must be an object")
    if require_non_empty and not value:
        raise UploadError("invalid_participant_mapping", "mapping must not be empty")
    if len(value) > MAX_PARTICIPANT_MAPPINGS:
        raise UploadError(
            "invalid_participant_mapping",
            f"mapping cannot contain more than {MAX_PARTICIPANT_MAPPINGS} participants",
        )

    normalized: dict[str, str] = {}
    for source_id, role in value.items():
        if not isinstance(source_id, str):
            raise UploadError("invalid_participant_mapping", "participant IDs must be strings")
        cleaned_source_id = source_id.strip()
        if (
            not cleaned_source_id
            or len(cleaned_source_id) > MAX_PARTICIPANT_ID_CHARACTERS
            or any(not character.isprintable() for character in cleaned_source_id)
        ):
            raise UploadError(
                "invalid_participant_mapping",
                f"participant IDs must be 1-{MAX_PARTICIPANT_ID_CHARACTERS} printable characters",
            )
        if cleaned_source_id in normalized:
            raise UploadError("invalid_participant_mapping", "participant IDs must be unique")
        if not isinstance(role, str) or role not in PARTICIPANT_ROLES:
            raise UploadError(
                "invalid_participant_mapping",
                "participant role must be persona, user, other, or unknown",
            )
        normalized[cleaned_source_id] = role
    return normalized
