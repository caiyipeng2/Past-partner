"""Replaceable object-byte storage boundary.

The protocol deliberately speaks in logical keys. Filesystem layout, encryption
envelopes, and metadata transactions remain owned by their existing services.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from pathlib import PureWindowsPath
from typing import BinaryIO, Iterator, Protocol, runtime_checkable
from uuid import uuid4

from src.services.storage import StorageLayout


_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_COPY_BLOCK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class BlobReceipt:
    """Metadata confirmed by a successful object write."""

    key: str
    length: int
    sha256: str


class StorageError(ValueError):
    """Stable adapter error whose message is safe to expose at the service boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class InvalidKeyError(StorageError):
    def __init__(self, message: str = "object key is invalid"):
        super().__init__("invalid_key", message)


class ObjectNotFoundError(StorageError):
    def __init__(self, message: str = "object was not found"):
        super().__init__("object_not_found", message)


class ObjectConflictError(StorageError):
    def __init__(self, message: str = "object already exists"):
        super().__init__("object_conflict", message)


class StorageReadError(StorageError):
    def __init__(self, message: str = "object could not be read"):
        super().__init__("storage_read_failed", message)


class StorageWriteError(StorageError):
    def __init__(self, message: str = "object could not be written"):
        super().__init__("storage_write_failed", message)


class StorageBackendUnsupportedError(StorageError):
    def __init__(self, message: str = "storage backend is unsupported"):
        super().__init__("storage_backend_unsupported", message)


@runtime_checkable
class BlobStore(Protocol):
    def put(
        self,
        key: str,
        source: BinaryIO,
        *,
        length: int,
        sha256: str,
    ) -> BlobReceipt: ...

    def iter_bytes(self, key: str, *, block_bytes: int) -> Iterator[bytes]: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> bool: ...


class LocalBlobStore:
    """Atomic filesystem adapter behind the logical-key storage boundary."""

    def __init__(self, layout: StorageLayout):
        self.layout = layout
        self._commit_lock = threading.RLock()

    def put(
        self,
        key: str,
        source: BinaryIO,
        *,
        length: int,
        sha256: str,
    ) -> BlobReceipt:
        destination = self._resolve_key(key)
        expected_length, expected_digest = self._validate_write_metadata(length, sha256)
        if not hasattr(source, "read"):
            raise StorageReadError()

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageWriteError() from exc

        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            digest = hashlib.sha256()
            actual_length = 0
            try:
                with temporary.open("xb") as output:
                    while True:
                        try:
                            chunk = source.read(_COPY_BLOCK_BYTES)
                        except (OSError, ValueError) as exc:
                            raise StorageReadError() from exc
                        if chunk is None:
                            raise StorageReadError()
                        try:
                            chunk = bytes(chunk)
                        except (TypeError, ValueError) as exc:
                            raise StorageReadError() from exc
                        if not chunk:
                            break
                        actual_length += len(chunk)
                        if actual_length > expected_length:
                            raise StorageWriteError("source length exceeds declared length")
                        output.write(chunk)
                        digest.update(chunk)

                    if actual_length != expected_length:
                        raise StorageWriteError("source length does not match declared length")
                    if digest.hexdigest() != expected_digest:
                        raise StorageWriteError("source digest does not match declared digest")
                    output.flush()
                    os.fsync(output.fileno())
            except StorageError:
                raise
            except OSError as exc:
                raise StorageWriteError() from exc

            with self._commit_lock:
                if destination.exists():
                    raise ObjectConflictError()
                try:
                    os.replace(temporary, destination)
                except OSError as exc:
                    raise StorageWriteError() from exc
            return BlobReceipt(key=key, length=actual_length, sha256=expected_digest)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # The primary storage error is more useful than cleanup noise. A
                # subsequent startup cleanup can remove an orphaned temp file.
                pass

    def iter_bytes(self, key: str, *, block_bytes: int) -> Iterator[bytes]:
        path = self._resolve_key(key)
        if isinstance(block_bytes, bool) or not isinstance(block_bytes, int) or block_bytes <= 0:
            raise StorageReadError("read block size must be positive")
        try:
            source = path.open("rb")
        except FileNotFoundError as exc:
            raise ObjectNotFoundError() from exc
        except OSError as exc:
            raise StorageReadError() from exc

        try:
            while True:
                try:
                    chunk = source.read(block_bytes)
                except OSError as exc:
                    raise StorageReadError() from exc
                if not chunk:
                    break
                yield chunk
        finally:
            source.close()

    def exists(self, key: str) -> bool:
        path = self._resolve_key(key)
        try:
            return path.is_file()
        except OSError as exc:
            raise StorageReadError() from exc

    def delete(self, key: str) -> bool:
        path = self._resolve_key(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageWriteError() from exc
        return True

    @staticmethod
    def _validate_write_metadata(length: int, sha256: str) -> tuple[int, str]:
        if isinstance(length, bool) or not isinstance(length, int) or length < 0:
            raise StorageWriteError("declared length is invalid")
        if not isinstance(sha256, str) or _SHA256.fullmatch(sha256) is None:
            raise StorageWriteError("declared digest is invalid")
        return length, sha256.lower()

    def _resolve_key(self, key: str) -> Path:
        if not isinstance(key, str) or not key or "\x00" in key:
            raise InvalidKeyError()
        if key.startswith(("/", "\\")) or "\\" in key:
            raise InvalidKeyError()
        if any(ord(character) < 32 for character in key):
            raise InvalidKeyError()
        windows_key = PureWindowsPath(key)
        if windows_key.drive or windows_key.root or windows_key.anchor:
            raise InvalidKeyError()

        segments = key.split("/")
        if any(not segment or segment in {".", ".."} or ":" in segment for segment in segments):
            raise InvalidKeyError()
        candidate = (self.layout.root.joinpath(*segments)).resolve()
        try:
            candidate.relative_to(self.layout.root)
        except ValueError as exc:
            raise InvalidKeyError() from exc
        return candidate


def build_blob_store(backend: str, layout: StorageLayout) -> BlobStore:
    """Build only an explicitly registered backend; never silently fall back."""

    if backend == "local":
        return LocalBlobStore(layout)
    raise StorageBackendUnsupportedError()


__all__ = [
    "BlobReceipt",
    "BlobStore",
    "build_blob_store",
    "InvalidKeyError",
    "LocalBlobStore",
    "ObjectConflictError",
    "ObjectNotFoundError",
    "StorageBackendUnsupportedError",
    "StorageError",
    "StorageReadError",
    "StorageWriteError",
]
