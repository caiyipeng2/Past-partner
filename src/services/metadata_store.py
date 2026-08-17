"""Storage port for encrypted application metadata.

Repositories depend on this small lifecycle boundary instead of choosing a
database driver themselves.  The connection protocol intentionally describes
only the operations the repositories use, keeping concrete driver types out of
the port module and leaving room for a later PostgreSQL adapter.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable


class MetadataStoreError(RuntimeError):
    """Stable, non-sensitive error raised by a metadata backend."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@runtime_checkable
class MetadataConnection(Protocol):
    @property
    def in_transaction(self) -> bool: ...

    def execute(self, sql: str, parameters: Any = ...) -> Any: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class MetadataStore(Protocol):
    backend_name: str

    def migrate(self) -> int: ...

    def connect(self) -> MetadataConnection: ...

    def transaction(self, *, immediate: bool = False) -> Iterator[MetadataConnection]: ...


def metadata_store_from_path(database_path: Path | str) -> MetadataStore:
    """Explicit compatibility factory for legacy path-based constructors."""

    from src.services.sqlite_metadata_store import SQLiteMetadataStore

    return SQLiteMetadataStore(database_path)


def require_metadata_store(value: MetadataStore | Path | str) -> MetadataStore:
    """Normalize an injected store while retaining legacy repository APIs."""

    if isinstance(value, (Path, str)):
        return metadata_store_from_path(value)
    if isinstance(value, MetadataStore):
        return value
    raise TypeError("metadata_store must implement the MetadataStore contract")


def build_metadata_store(backend: str, database_path: Path | str) -> MetadataStore:
    """Build the configured metadata backend without silently falling back."""

    if backend != "sqlite":
        raise MetadataStoreError("metadata_backend_unsupported", "metadata backend is unsupported")
    from src.services.sqlite_metadata_store import SQLiteMetadataStore

    return SQLiteMetadataStore(database_path)
