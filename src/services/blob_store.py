"""Replaceable object-byte storage boundary.

The protocol deliberately speaks in logical keys. Filesystem layout, encryption
envelopes, and metadata transactions remain owned by their existing services.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Iterator, Protocol, runtime_checkable


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


__all__ = [
    "BlobReceipt",
    "BlobStore",
    "InvalidKeyError",
    "ObjectConflictError",
    "ObjectNotFoundError",
    "StorageBackendUnsupportedError",
    "StorageError",
    "StorageReadError",
    "StorageWriteError",
]
