"""PostgreSQL implementation of the metadata storage port.

The rest of the service still emits the small SQLite-shaped SQL vocabulary used
by the existing repositories.  This adapter owns the dialect conversion and
the pooled connection lifecycle so repositories never need to know which
driver is active.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from typing import Any, Iterator

from src.services.metadata_store import (
    MetadataConnection,
    MetadataIntegrityError,
    MetadataOperationalError,
    MetadataStoreError,
)


_BEGIN_IMMEDIATE = re.compile(r"^BEGIN\s+IMMEDIATE\b", re.IGNORECASE)


def _load_pool_factory() -> Callable[..., Any]:
    try:
        from psycopg_pool import ConnectionPool
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("psycopg_pool is not installed") from exc
    return ConnectionPool


def _normalize_parameter(value: Any) -> Any:
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, tuple):
        return tuple(_normalize_parameter(item) for item in value)
    if isinstance(value, list):
        return [_normalize_parameter(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _normalize_parameter(item) for key, item in value.items()}
    return value


def _convert_qmarks(sql: str) -> str:
    """Convert qmark placeholders without touching quoted strings/comments."""

    output: list[str] = []
    state = "normal"
    index = 0
    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""
        if state == "normal":
            if char == "'":
                state = "single"
            elif char == '"':
                state = "double"
            elif char == "-" and next_char == "-":
                state = "line_comment"
            elif char == "/" and next_char == "*":
                state = "block_comment"
            elif char == "?":
                output.append("%s")
                index += 1
                continue
        elif state == "single":
            if char == "'":
                if next_char == "'":
                    output.extend((char, next_char))
                    index += 2
                    continue
                state = "normal"
        elif state == "double":
            if char == '"':
                if next_char == '"':
                    output.extend((char, next_char))
                    index += 2
                    continue
                state = "normal"
        elif state == "line_comment" and char in "\r\n":
            state = "normal"
        elif state == "block_comment" and char == "*" and next_char == "/":
            output.extend((char, next_char))
            index += 2
            state = "normal"
            continue
        output.append(char)
        index += 1
    return "".join(output)


def _convert_sql(sql: str) -> str:
    leading = sql[: len(sql) - len(sql.lstrip())]
    stripped = sql.lstrip()
    begin_match = _BEGIN_IMMEDIATE.match(stripped)
    if begin_match:
        stripped = "BEGIN" + stripped[begin_match.end() :]
        sql = leading + stripped
    return _convert_qmarks(sql)


def _is_integrity_error(error: Exception) -> bool:
    sqlstate = getattr(error, "sqlstate", None)
    if isinstance(sqlstate, str) and sqlstate.startswith("23"):
        return True
    name = type(error).__name__.lower()
    return any(
        marker in name
        for marker in ("integrity", "uniqueviolation", "foreignkeyviolation", "checkviolation")
    )


def _map_driver_error(error: Exception) -> MetadataStoreError:
    if _is_integrity_error(error):
        return MetadataIntegrityError()
    return MetadataOperationalError()


class _PostgreSQLConnection:
    def __init__(self, raw: Any, release: Callable[[Any, Any, Any], Any]) -> None:
        self._raw = raw
        self._release = release
        self._closed = False

    @property
    def in_transaction(self) -> bool:
        return bool(self._raw.in_transaction)

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        try:
            return self._raw.execute(_convert_sql(sql), _normalize_parameter(parameters))
        except Exception as exc:
            raise _map_driver_error(exc) from exc

    def commit(self) -> None:
        try:
            self._raw.commit()
        except Exception as exc:
            raise _map_driver_error(exc) from exc

    def rollback(self) -> None:
        try:
            self._raw.rollback()
        except Exception as exc:
            raise _map_driver_error(exc) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._release(None, None, None)
        except Exception as exc:
            raise _map_driver_error(exc) from exc


class PostgreSQLMetadataStore:
    """Pooled PostgreSQL adapter with a deliberately lazy optional import."""

    backend_name = "postgresql"

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 4,
        pool_factory: Callable[..., Any] | None = None,
        driver_loader: Callable[[], Callable[..., Any]] | None = None,
    ) -> None:
        if not isinstance(dsn, str) or not dsn.strip():
            raise MetadataStoreError("metadata_dsn_required", "metadata DSN is required")
        if min_size < 1 or max_size < min_size:
            raise MetadataStoreError("metadata_pool_invalid", "metadata pool size is invalid")
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool_factory = pool_factory
        self._driver_loader = driver_loader or _load_pool_factory
        self._pool: Any | None = None
        self._closed = False

    def migrate(self) -> int:
        raise MetadataStoreError(
            "metadata_migration_unavailable",
            "metadata migration is unavailable",
        )

    def _ensure_pool(self) -> Any:
        if self._closed:
            raise MetadataStoreError("metadata_store_closed", "metadata store is closed")
        if self._pool is not None:
            return self._pool
        try:
            factory = self._pool_factory or self._driver_loader()
        except ModuleNotFoundError as exc:
            raise MetadataStoreError(
                "metadata_driver_unavailable",
                "metadata PostgreSQL driver is unavailable",
            ) from exc
        try:
            pool = factory(
                conninfo=self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
                open=False,
            )
            pool.open(wait=True)
        except Exception as exc:
            raise _map_driver_error(exc) from exc
        self._pool = pool
        return pool

    def connect(self) -> MetadataConnection:
        pool = self._ensure_pool()
        try:
            checkout = pool.connection()
            raw = checkout.__enter__()
        except Exception as exc:
            raise _map_driver_error(exc) from exc
        return _PostgreSQLConnection(raw, checkout.__exit__)

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[MetadataConnection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()
            raise
        else:
            connection.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        pool, self._pool = self._pool, None
        if pool is None:
            return
        try:
            pool.close()
        except Exception as exc:
            raise _map_driver_error(exc) from exc
