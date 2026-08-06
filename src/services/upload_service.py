"""Resumable chunk storage with bounded memory and explicit integrity checks."""

from __future__ import annotations

import hashlib
import os
import re
import threading
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
from src.services.import_repository import ImportRepositoryError
from src.services.import_service import ImportJob, ImportService, ImportState
from src.preprocessing.parser_registry import ParserError, ParserRegistry
from src.services.storage import StorageLayout


DEFAULT_CHUNK_BYTES = 8 * 1024**2
DEFAULT_READ_BLOCK_BYTES = 64 * 1024
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


class UploadService:
    def __init__(
        self,
        storage: StorageLayout,
        imports: ImportService,
        encryption: AuthenticatedEncryptionService,
        max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        read_block_bytes: int = DEFAULT_READ_BLOCK_BYTES,
        parsers: ParserRegistry | None = None,
    ):
        if max_chunk_bytes <= 0 or read_block_bytes <= 0:
            raise ValueError("chunk and read block limits must be positive")
        if max_chunk_bytes > encryption.max_plaintext_bytes:
            raise ValueError("chunk limit cannot exceed the encryption segment limit")
        self.storage = storage
        self.imports = imports
        self.encryption = encryption
        self.max_chunk_bytes = max_chunk_bytes
        self.read_block_bytes = min(read_block_bytes, max_chunk_bytes)
        self.parsers = parsers or ParserRegistry.with_builtins()
        # The development runtime is one process. The lock prevents two request
        # threads from racing the same JSON manifest; production will replace
        # this with transactional metadata storage.
        self._lock = threading.RLock()

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
            destination = self._chunk_path(import_id, index)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            try:
                with temporary.open("xb") as output:
                    output.write(encrypted)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

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
                destination.unlink(missing_ok=True)
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
            if job.state is ImportState.UPLOADED and self.payload_path(import_id).is_file():
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

            destination = self.payload_path(import_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            digest = hashlib.sha256()
            try:
                with temporary.open("xb") as output:
                    for index in indexes:
                        part = self._chunk_path(import_id, index)
                        if not part.is_file():
                            raise UploadError("chunk_missing", f"stored chunk {index} is missing")
                        entry = entries[index]
                        encrypted_length = self._encrypted_length(entry)
                        with part.open("rb") as source:
                            encrypted = self._read_exact(source, encrypted_length, "chunk_corrupt")
                            if source.read(1):
                                raise UploadError("chunk_corrupt", "stored chunk has trailing bytes")
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
                        digest.update(plaintext)
                    final = self._encrypt_segment(
                        b"", self.chunk_aad(import_id, len(indexes), final=True), "payload_encryption_failed"
                    )
                    output.write(final)
                    output.flush()
                    os.fsync(output.fileno())
                if expected_digest is not None and digest.hexdigest() != expected_digest:
                    raise UploadError("payload_digest_mismatch", "completed payload digest does not match")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

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
                destination.unlink(missing_ok=True)
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
                self._chunk_path(import_id, index).unlink(missing_ok=True)
            self.payload_path(import_id).unlink(missing_ok=True)
            return cancelled

    def delete_import(
        self,
        owner_id: str,
        import_id: str | None = None,
    ) -> dict[str, Any]:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        with self._lock:
            job = self.imports.get(owner_id, import_id)
            if job.state is ImportState.PROCESSING:
                raise UploadError(
                    "deletion_unavailable",
                    "processing imports cannot be deleted",
                )
            manifest = self._load_manifest(owner_id, import_id)
            indexes = self._manifest_indexes(manifest)
            try:
                for index in indexes:
                    self._chunk_path(import_id, index).unlink(missing_ok=True)
                self.payload_path(import_id).unlink(missing_ok=True)
            except OSError as exc:
                raise UploadError(
                    "deletion_failed",
                    "import files could not be removed",
                ) from exc

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

    def delete_persona_imports(self, owner_id: str, persona_id: str) -> int:
        with self._lock:
            jobs = self.imports.list_for_persona(owner_id, persona_id)
            for job in jobs:
                self.delete_import(owner_id, job.id)
            return len(jobs)

    def payload_path(self, import_id: str) -> Path:
        return self.storage.object_path("payloads", import_id, ".bin")

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
            if job.state is not ImportState.UPLOADED or not self.payload_path(import_id).is_file():
                raise UploadError(
                    "preview_unavailable",
                    "preview requires a completed uploaded import",
                )
            if len(job.files) > 1:
                raise UploadError(
                    "preview_multi_file_unsupported",
                    "preview currently requires a single-file import",
                )

            source_name = job.files[0].source_name if job.files else job.source_name
            media_type = job.files[0].media_type if job.files else job.media_type
            manifest = self._load_manifest(owner_id, import_id)
            participant_mapping = _normalize_participant_mapping(manifest.get("participant_mapping"))
            corrections = _normalize_corrections(manifest.get("corrections"))
            preview_id = uuid4().hex
            destination = self.storage.object_path("preview", preview_id, ".bin")
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with destination.open("xb") as output:
                    for chunk in self.iter_payload(owner_id, import_id):
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    result = self.parsers.parse(
                        destination,
                        {
                            "source_name": source_name,
                            "media_type": media_type,
                            "record_id_namespace": job.id,
                        },
                        max_records=max_records,
                    )
                except ParserError as exc:
                    raise UploadError(exc.code, str(exc)) from exc
                records: list[dict[str, Any]] = []
                for index, record in enumerate(result.records):
                    record_id = record.record_id or _record_id(job.id, result.source_type, index)
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
                    records.append(preview_record)
                return {
                    "import_id": job.id,
                    "state": job.state.value,
                    "source_name": source_name,
                    "media_type": media_type,
                    "source_type": result.source_type,
                    "summary": dict(result.summary),
                    "warnings": list(result.warnings),
                    "records": records,
                }
            finally:
                destination.unlink(missing_ok=True)

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
            if job.state is not ImportState.UPLOADED or not self.payload_path(import_id).is_file():
                raise UploadError(
                    "correction_unavailable",
                    "corrections require a completed uploaded import",
                )
            if len(job.files) > 1:
                raise UploadError(
                    "corrections_multi_file_unsupported",
                    "corrections currently require a single-file import",
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
            if job.state is not ImportState.UPLOADED or not self.payload_path(import_id).is_file():
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
        with self._lock:
            job = self.imports.get(owner_id, import_id)
            if job.state is not ImportState.UPLOADED or not self.payload_path(import_id).is_file():
                raise UploadError("payload_unavailable", "completed encrypted payload is unavailable")
            manifest = self._load_manifest(owner_id, import_id)
            chunks: Mapping[str, Mapping[str, Any]] = manifest["chunks"]
            indexes = sorted(int(value) for value in chunks)
            entries = {index: self._chunk_entry(chunks[str(index)]) for index in indexes}
            if indexes != list(range(len(indexes))):
                raise UploadError("manifest_corrupt", "encrypted chunk indexes are not contiguous")

            with self.payload_path(import_id).open("rb") as source:
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
                            "payload_authentication_failed", "payload chunk authentication failed"
                        ) from exc
                    if len(plaintext) != int(entry["length"]):
                        raise UploadError("payload_corrupt", "payload chunk length is invalid")
                    if hashlib.sha256(plaintext).hexdigest() != str(entry["sha256"]).lower():
                        raise UploadError("payload_corrupt", "payload chunk digest is invalid")
                    yield plaintext

                final_length = self._encrypted_length_value(manifest.get("final_encrypted_length"))
                final = self._read_exact(source, final_length, "payload_corrupt")
                try:
                    sentinel = self.encryption.decrypt(
                        final, self.chunk_aad(import_id, len(indexes), final=True)
                    )
                except (AuthenticationError, InvalidEncryptedPayloadError) as exc:
                    raise UploadError(
                        "payload_authentication_failed", "payload end marker authentication failed"
                    ) from exc
                if sentinel:
                    raise UploadError("payload_corrupt", "payload end marker is not empty")
                if source.read(1):
                    raise UploadError("payload_corrupt", "payload has trailing bytes")

    def _chunk_path(self, import_id: str, index: int) -> Path:
        return self.storage.object_path("upload-parts", f"{import_id}-{index}", ".part")

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
