"""PostgreSQL migration ledger for the shared metadata schema."""

from __future__ import annotations

import re
from typing import Callable, Iterable

from src.services.database import DEFAULT_MIGRATIONS, Migration, SchemaHistoryError
from src.services.metadata_store import MetadataConnection


_BLOB_TYPE = re.compile(r"\bBLOB\b", re.IGNORECASE)


def _history_text(value: object, field: str) -> str:
    """Normalize driver text results without stringifying byte literals.

    PostgreSQL clusters initialized with ``SQL_ASCII`` can make psycopg return
    textual columns as bytes.  ``str(bytes_value)`` would turn a valid name into
    ``"b'... '"`` and make a clean migration ledger look corrupt.  Migration
    names and checksums are ASCII by contract, while UTF-8 decoding keeps the
    helper compatible with normal text clusters and rejects malformed history
    without exposing driver details.
    """

    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SchemaHistoryError(f"migration history {field} is invalid") from exc
    if isinstance(value, str):
        return value
    raise SchemaHistoryError(f"migration history {field} is invalid")


class PostgreSQLMigrator:
    """Apply the logical metadata migrations to PostgreSQL atomically.

    Checksums intentionally use the original logical SQLite statements.  The
    PostgreSQL compiler changes only dialect tokens, so both backends share a
    migration identity while keeping independent ledgers and data stores.
    """

    def __init__(
        self,
        connect: Callable[[], MetadataConnection],
        migrations: Iterable[Migration] = DEFAULT_MIGRATIONS,
    ) -> None:
        self._connect = connect
        self.migrations = tuple(migrations)
        self._validate_plan()

    def migrate(self) -> int:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            self._ensure_history_table(connection)
            rows = connection.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            applied = {
                int(version): (
                    _history_text(name, "name"),
                    _history_text(checksum, "checksum"),
                )
                for version, name, checksum in rows
            }
            self._validate_history(applied)

            for migration in self.migrations:
                if migration.version in applied:
                    continue
                statements = migration.postgres_statements or migration.statements
                for statement in statements:
                    connection.execute(self._compile_statement(statement))
                if migration.post_apply is not None:
                    migration.post_apply(connection)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, migration.checksum),
                )

            connection.commit()
            return self.migrations[-1].version
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _validate_plan(self) -> None:
        if not self.migrations:
            raise ValueError("migration plan must contain at least one migration")
        versions = tuple(migration.version for migration in self.migrations)
        if versions != tuple(range(1, len(self.migrations) + 1)):
            raise ValueError("migration versions must be contiguous and start at 1")
        names = tuple(migration.name for migration in self.migrations)
        if len(names) != len(set(names)):
            raise ValueError("migration names must be unique")

    def _validate_history(self, applied: dict[int, tuple[str, str]]) -> None:
        expected = {migration.version: migration for migration in self.migrations}
        for version, (name, checksum) in applied.items():
            if version not in expected:
                raise SchemaHistoryError(f"database has unknown migration version {version}")
            migration = expected[version]
            if migration.name != name:
                raise SchemaHistoryError(
                    f"migration {version} is recorded as {name!r}, expected {migration.name!r}"
                )
            if migration.checksum != checksum:
                raise SchemaHistoryError(f"migration {version} checksum does not match its history")

        versions = tuple(applied)
        if versions and versions != tuple(range(1, max(versions) + 1)):
            raise SchemaHistoryError("database migration history contains a version gap")

    @staticmethod
    def _compile_statement(statement: str) -> str:
        return _BLOB_TYPE.sub("BYTEA", statement)

    @staticmethod
    def _ensure_history_table(connection: MetadataConnection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY CHECK (version > 0),
                name TEXT NOT NULL UNIQUE,
                checksum TEXT NOT NULL CHECK (length(checksum) = 64),
                applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
