"""SQLite implementation of the metadata storage port."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.services.database import SQLiteMigrator
from src.services.metadata_store import (
    MetadataConnection,
    MetadataIntegrityError,
    MetadataOperationalError,
    MetadataStoreError,
)


class _SQLiteMetadataConnection:
    """Hide sqlite3 exception classes behind the metadata port."""

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw

    @property
    def in_transaction(self) -> bool:
        return self._raw.in_transaction

    def execute(self, sql: str, parameters: object = ()) -> object:
        try:
            return self._raw.execute(sql, parameters)
        except sqlite3.IntegrityError as exc:
            raise MetadataIntegrityError() from exc
        except sqlite3.Error as exc:
            raise MetadataOperationalError() from exc

    def commit(self) -> None:
        try:
            self._raw.commit()
        except sqlite3.Error as exc:
            raise MetadataOperationalError() from exc

    def rollback(self) -> None:
        try:
            self._raw.rollback()
        except sqlite3.Error as exc:
            raise MetadataOperationalError() from exc

    def close(self) -> None:
        try:
            self._raw.close()
        except sqlite3.Error as exc:
            raise MetadataOperationalError() from exc


class SQLiteMetadataStore:
    """Own migration and connection policy for the local encrypted database."""

    backend_name = "sqlite"

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self._migrator = SQLiteMigrator(self.database_path)
        self._connect_impl = self._connect

    def close(self) -> None:
        """Close the adapter lifecycle; SQLite connections are per-operation."""

        return None

    def migrate(self) -> int:
        try:
            return self._migrator.migrate()
        except Exception as exc:
            raise MetadataStoreError(
                "metadata_migration_failed", "metadata store migration failed"
            ) from exc

    def connect(self) -> MetadataConnection:
        try:
            return _SQLiteMetadataConnection(self._connect_impl())
        except (OSError, sqlite3.Error) as exc:
            raise MetadataStoreError(
                "metadata_connection_failed", "metadata store connection failed"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[MetadataConnection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
