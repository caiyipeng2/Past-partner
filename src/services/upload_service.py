"""Resumable chunk storage with bounded memory and explicit integrity checks."""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Mapping
from uuid import uuid4

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
        max_chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        read_block_bytes: int = DEFAULT_READ_BLOCK_BYTES,
    ):
        if max_chunk_bytes <= 0 or read_block_bytes <= 0:
            raise ValueError("chunk and read block limits must be positive")
        self.storage = storage
        self.imports = imports
        self.max_chunk_bytes = max_chunk_bytes
        self.read_block_bytes = min(read_block_bytes, max_chunk_bytes)
        # The development runtime is one process. The lock prevents two request
        # threads from racing the same JSON manifest; production will replace
        # this with transactional metadata storage.
        self._lock = threading.RLock()

    def put_chunk(
        self,
        import_id: str,
        index: int,
        declared_length: int,
        sha256: str,
        stream: BinaryIO,
    ) -> ChunkReceipt:
        index = self._validate_index(index)
        declared_length = self._validate_length(declared_length)
        digest = self._validate_digest(sha256)

        with self._lock:
            job = self.imports.get(import_id)
            if job.state in {ImportState.UPLOADED, ImportState.PROCESSING, ImportState.COMPLETED}:
                raise UploadError("upload_closed", "the import no longer accepts chunks")

            manifest = self._load_manifest(import_id)
            chunks = manifest["chunks"]
            existing = chunks.get(str(index))
            if existing is not None:
                if existing["length"] != declared_length or existing["sha256"] != digest:
                    raise UploadError("chunk_conflict", "chunk index already has different content")
                actual_digest = self._consume_and_hash(stream, declared_length)
                if actual_digest != digest:
                    raise UploadError("chunk_digest_mismatch", "chunk digest does not match its body")
                return self._receipt(job, index, declared_length, digest, duplicate=True)

            received_bytes = sum(int(item["length"]) for item in chunks.values())
            if received_bytes + declared_length > job.total_bytes:
                raise UploadError("import_size_exceeded", "chunk exceeds the import's declared size")

            destination = self._chunk_path(import_id, index)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
            try:
                actual_digest = self._write_and_hash(stream, declared_length, temporary)
                if actual_digest != digest:
                    raise UploadError("chunk_digest_mismatch", "chunk digest does not match its body")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

            chunks[str(index)] = {"length": declared_length, "sha256": digest}
            self.storage.write_json("upload-manifests", import_id, manifest)
            received_bytes += declared_length
            updated = replace(
                job,
                received_bytes=received_bytes,
                chunk_count=len(chunks),
                state=ImportState.UPLOADING,
                updated_at=datetime.now(UTC).isoformat(),
            )
            self.imports.save(updated)
            return self._receipt(updated, index, declared_length, digest, duplicate=False)

    def complete(self, import_id: str, whole_sha256: str | None = None) -> ImportJob:
        expected_digest = self._validate_digest(whole_sha256) if whole_sha256 is not None else None
        with self._lock:
            job = self.imports.get(import_id)
            if job.state is ImportState.UPLOADED and self.payload_path(import_id).is_file():
                return job
            if job.state in {ImportState.PROCESSING, ImportState.COMPLETED}:
                raise UploadError("upload_closed", "the import is already being processed")

            manifest = self._load_manifest(import_id)
            chunks: Mapping[str, Mapping[str, Any]] = manifest["chunks"]
            indexes = sorted(int(value) for value in chunks)
            total = sum(int(chunks[str(index)]["length"]) for index in indexes)
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
                        with part.open("rb") as source:
                            while block := source.read(self.read_block_bytes):
                                output.write(block)
                                digest.update(block)
                    output.flush()
                    os.fsync(output.fileno())
                if expected_digest is not None and digest.hexdigest() != expected_digest:
                    raise UploadError("payload_digest_mismatch", "completed payload digest does not match")
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)

            completed = replace(
                job,
                state=ImportState.UPLOADED,
                updated_at=datetime.now(UTC).isoformat(),
            )
            self.imports.save(completed)
            return completed

    def payload_path(self, import_id: str) -> Path:
        return self.storage.object_path("payloads", import_id, ".bin")

    def _chunk_path(self, import_id: str, index: int) -> Path:
        return self.storage.object_path("upload-parts", f"{import_id}-{index}", ".part")

    def _load_manifest(self, import_id: str) -> dict[str, Any]:
        try:
            value = self.storage.read_json("upload-manifests", import_id)
        except FileNotFoundError:
            return {"version": 1, "import_id": import_id, "chunks": {}}
        if not isinstance(value, dict) or not isinstance(value.get("chunks"), dict):
            raise UploadError("manifest_corrupt", "upload manifest is invalid")
        return value

    def _write_and_hash(self, stream: BinaryIO, length: int, destination: Path) -> str:
        digest = hashlib.sha256()
        remaining = length
        with destination.open("xb") as output:
            while remaining:
                block = stream.read(min(self.read_block_bytes, remaining))
                if not block:
                    raise UploadError("chunk_length_mismatch", "chunk ended before its declared length")
                if len(block) > remaining:
                    raise UploadError("chunk_length_mismatch", "chunk exceeded its declared length")
                output.write(block)
                digest.update(block)
                remaining -= len(block)
            output.flush()
            os.fsync(output.fileno())
        return digest.hexdigest()

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
