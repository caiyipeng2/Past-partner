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
from typing import Any, BinaryIO, Iterator, Protocol, runtime_checkable
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


@dataclass(frozen=True, slots=True)
class S3BlobStoreSettings:
    bucket: str
    region: str = "us-east-1"
    endpoint: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    session_token: str | None = None
    path_style: bool = True


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


class StorageBackendUnavailableError(StorageError):
    def __init__(self, message: str = "storage backend is unavailable"):
        super().__init__("storage_backend_unavailable", message)


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
        normalized_key = _validate_key(key)
        candidate = (self.layout.root.joinpath(*normalized_key.split("/"))).resolve()
        try:
            candidate.relative_to(self.layout.root)
        except ValueError as exc:
            raise InvalidKeyError() from exc
        return candidate


class S3BlobStore:
    """S3-compatible adapter that keeps the upload contract stream-oriented."""

    def __init__(self, client: Any, settings: S3BlobStoreSettings):
        self._client = client
        self.settings = settings

    def put(
        self,
        key: str,
        source: BinaryIO,
        *,
        length: int,
        sha256: str,
    ) -> BlobReceipt:
        key = _validate_key(key)
        expected_length, expected_digest = LocalBlobStore._validate_write_metadata(length, sha256)
        if not hasattr(source, "read"):
            raise StorageReadError()
        reader = _HashingReader(source, expected_length)
        try:
            self._client.put_object(
                Bucket=self.settings.bucket,
                Key=key,
                Body=reader,
                ContentLength=expected_length,
                Metadata={"sha256": expected_digest},
                IfNoneMatch="*",
            )
            if reader.actual_length != expected_length or reader.has_extra():
                self._discard_after_write(key)
                raise StorageWriteError("source length does not match declared length")
            if reader.hexdigest != expected_digest:
                self._discard_after_write(key)
                raise StorageWriteError("source digest does not match declared digest")
        except StorageError:
            raise
        except Exception as exc:
            raise _map_remote_error(exc, write=True) from exc
        return BlobReceipt(key=key, length=expected_length, sha256=expected_digest)

    def iter_bytes(self, key: str, *, block_bytes: int) -> Iterator[bytes]:
        key = _validate_key(key)
        if isinstance(block_bytes, bool) or not isinstance(block_bytes, int) or block_bytes <= 0:
            raise StorageReadError("read block size must be positive")
        body = None
        try:
            response = self._client.get_object(Bucket=self.settings.bucket, Key=key)
            body = response["Body"]
            if hasattr(body, "iter_chunks"):
                for chunk in body.iter_chunks(chunk_size=block_bytes):
                    if chunk:
                        yield bytes(chunk)
            else:
                while True:
                    chunk = body.read(block_bytes)
                    if not chunk:
                        break
                    yield bytes(chunk)
        except StorageError:
            raise
        except Exception as exc:
            raise _map_remote_error(exc, write=False) from exc
        finally:
            if body is not None and hasattr(body, "close"):
                body.close()

    def exists(self, key: str) -> bool:
        key = _validate_key(key)
        try:
            self._client.head_object(Bucket=self.settings.bucket, Key=key)
            return True
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise _map_remote_error(exc, write=False) from exc

    def delete(self, key: str) -> bool:
        key = _validate_key(key)
        try:
            self._client.head_object(Bucket=self.settings.bucket, Key=key)
        except Exception as exc:
            if _is_not_found(exc):
                return False
            raise _map_remote_error(exc, write=True) from exc
        try:
            self._client.delete_object(Bucket=self.settings.bucket, Key=key)
        except Exception as exc:
            raise _map_remote_error(exc, write=True) from exc
        return True

    def _discard_after_write(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.settings.bucket, Key=key)
        except Exception:
            pass


class _HashingReader:
    def __init__(self, source: BinaryIO, expected_length: int):
        self._source = source
        self._expected_length = expected_length
        self._digest = hashlib.sha256()
        self.actual_length = 0

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()

    def tell(self) -> int:
        """Expose the logical stream offset expected by botocore checksums."""

        return self.actual_length

    def seek(self, offset: int, whence: int = 0) -> int:
        """Allow botocore to rewind its preflight checksum read to the start.

        The upload contract is forward-only, so arbitrary repositioning would
        require reconstructing the digest from an unknown prefix.  Supporting
        only the SDK's ``seek(0)`` call keeps the wrapper honest and resets both
        the source position and the tracked digest for the actual upload pass.
        """

        if offset != 0 or whence != 0 or not hasattr(self._source, "seek"):
            raise OSError("upload stream only supports rewinding to the start")
        try:
            self._source.seek(0)
        except (OSError, ValueError) as exc:
            raise OSError("upload stream cannot be rewound") from exc
        self.actual_length = 0
        self._digest = hashlib.sha256()
        return 0

    def read(self, size: int = -1) -> bytes:
        if self.actual_length >= self._expected_length:
            return b""
        if not isinstance(size, int) or size < 0:
            size = _COPY_BLOCK_BYTES
        size = min(size, self._expected_length - self.actual_length)
        try:
            chunk = self._source.read(size)
        except (OSError, ValueError) as exc:
            raise StorageReadError() from exc
        if chunk is None:
            raise StorageReadError()
        try:
            chunk = bytes(chunk)
        except (TypeError, ValueError) as exc:
            raise StorageReadError() from exc
        if len(chunk) > size:
            raise StorageWriteError("source length exceeds declared length")
        self.actual_length += len(chunk)
        self._digest.update(chunk)
        return chunk

    def has_extra(self) -> bool:
        try:
            extra = self._source.read(1)
        except (OSError, ValueError) as exc:
            raise StorageReadError() from exc
        return bool(extra)


def _validate_key(key: str) -> str:
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
    return key


def _remote_details(exc: Exception) -> tuple[int | None, str | None]:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None, None
    metadata = response.get("ResponseMetadata")
    error = response.get("Error")
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, dict) else None
    code = error.get("Code") if isinstance(error, dict) else None
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    return status, str(code) if code is not None else None


def _is_not_found(exc: Exception) -> bool:
    status, code = _remote_details(exc)
    return status == 404 or code in {"404", "NoSuchKey", "NotFound"}


def _map_remote_error(exc: Exception, *, write: bool) -> StorageError:
    status, code = _remote_details(exc)
    if _is_not_found(exc):
        return ObjectNotFoundError()
    if status == 412 or code in {"PreconditionFailed", "ConditionalRequestConflict"}:
        return ObjectConflictError()
    return StorageWriteError() if write else StorageReadError()


def build_blob_store(
    backend: str,
    layout: StorageLayout,
    *,
    s3_settings: S3BlobStoreSettings | None = None,
) -> BlobStore:
    """Build only an explicitly registered backend; never silently fall back."""

    if backend == "local":
        return LocalBlobStore(layout)
    if backend == "s3":
        if s3_settings is None:
            raise StorageBackendUnavailableError("S3 storage settings are missing")
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise StorageBackendUnavailableError() from exc
        client_kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": s3_settings.region,
        }
        if s3_settings.endpoint:
            client_kwargs["endpoint_url"] = s3_settings.endpoint
        if s3_settings.access_key is not None:
            client_kwargs["aws_access_key_id"] = s3_settings.access_key
            client_kwargs["aws_secret_access_key"] = s3_settings.secret_key
        if s3_settings.session_token is not None:
            client_kwargs["aws_session_token"] = s3_settings.session_token
        client_kwargs["config"] = Config(
            s3={"addressing_style": "path" if s3_settings.path_style else "auto"}
        )
        try:
            client = boto3.client(**client_kwargs)
        except Exception as exc:
            raise StorageBackendUnavailableError() from exc
        return S3BlobStore(client, s3_settings)
    raise StorageBackendUnsupportedError()


__all__ = [
    "BlobReceipt",
    "BlobStore",
    "build_blob_store",
    "InvalidKeyError",
    "LocalBlobStore",
    "S3BlobStore",
    "S3BlobStoreSettings",
    "ObjectConflictError",
    "ObjectNotFoundError",
    "StorageBackendUnsupportedError",
    "StorageBackendUnavailableError",
    "StorageError",
    "StorageReadError",
    "StorageWriteError",
]
