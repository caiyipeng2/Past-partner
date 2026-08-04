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

from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.import_repository import ImportRepositoryError
from src.services.import_service import ImportJob, ImportService, ImportState
from src.services.storage import StorageLayout


DEFAULT_CHUNK_BYTES = 8 * 1024**2
DEFAULT_READ_BLOCK_BYTES = 64 * 1024
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
