"""Versioned SQLite schema initialization for local persistence."""

from __future__ import annotations

import sqlite3
from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class SchemaHistoryError(RuntimeError):
    """Raised when an existing database no longer matches migration history."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version <= 0:
            raise ValueError("migration version must be positive")
        if not self.name or not self.name.isidentifier():
            raise ValueError("migration name must be a non-empty identifier")

    @property
    def checksum(self) -> str:
        digest = sha256()
        for component in (str(self.version), self.name, *self.statements):
            encoded = component.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, byteorder="big"))
            digest.update(encoded)
        return digest.hexdigest()


# Version 1 establishes the durable migration ledger. Version 2 owns the
# encrypted persona record table, version 3 owns import metadata, version 4
# owns local owner sessions, version 5 owns encrypted media consents, and version 6
# owns encrypted fine-tuning job metadata. Version 7 adds optimistic revisions for
# those jobs so concurrent HTTP requests cannot overwrite durable lifecycle state.
# Version 8 distinguishes loopback sessions from development device-pairing sessions
# and stores only the configured device-token fingerprint for rotation revocation.
# Version 9 adds encrypted owner/persona-scoped conversation envelopes. Message text
# never appears in SQLite columns; the persona index is only used for safe filtering
# and cascade deletion.
DEFAULT_MIGRATIONS = (
    Migration(version=1, name="bootstrap_schema", statements=()),
    Migration(
        version=2,
        name="persona_repository",
        statements=(
            """
            CREATE TABLE personas (
                id TEXT PRIMARY KEY,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
        ),
    ),
    Migration(
        version=3,
        name="import_repository",
        statements=(
            """
            CREATE TABLE imports (
                id TEXT PRIMARY KEY,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
            """
            CREATE TABLE import_manifests (
                import_id TEXT PRIMARY KEY REFERENCES imports(id) ON DELETE CASCADE,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
        ),
    ),
    Migration(
        version=4,
        name="local_auth_owner",
        statements=(
            """
            CREATE TABLE local_users (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL UNIQUE CHECK (kind = 'owner'),
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
            """
            CREATE TABLE local_sessions (
                token_hash BLOB PRIMARY KEY CHECK (length(token_hash) = 32),
                user_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            )
            """,
            "ALTER TABLE personas ADD COLUMN owner_id TEXT REFERENCES local_users(id)",
            "ALTER TABLE imports ADD COLUMN owner_id TEXT REFERENCES local_users(id)",
        ),
    ),
    Migration(
        version=5,
        name="media_consent_repository",
        statements=(
            """
            CREATE TABLE consents (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
            "CREATE INDEX consents_owner_persona_idx ON consents(owner_id, persona_id)",
        ),
    ),
    Migration(
        version=6,
        name="training_job_repository",
        statements=(
            """
            CREATE TABLE training_jobs (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
            "CREATE INDEX training_jobs_owner_persona_idx ON training_jobs(owner_id, persona_id)",
        ),
    ),
    Migration(
        version=7,
        name="training_job_revisions",
        statements=(
            "ALTER TABLE training_jobs ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0)",
        ),
    ),
    Migration(
        version=8,
        name="device_pairing_sessions",
        statements=(
            "ALTER TABLE local_sessions ADD COLUMN session_origin TEXT NOT NULL DEFAULT 'loopback' CHECK (session_origin IN ('loopback', 'device'))",
            "ALTER TABLE local_sessions ADD COLUMN pairing_token_fingerprint BLOB CHECK (pairing_token_fingerprint IS NULL OR length(pairing_token_fingerprint) = 32)",
        ),
    ),
    Migration(
        version=9,
        name="conversation_repository",
        statements=(
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
            "CREATE INDEX conversations_owner_persona_idx ON conversations(owner_id, persona_id)",
        ),
    ),
)
CURRENT_SCHEMA_VERSION = DEFAULT_MIGRATIONS[-1].version


class SQLiteMigrator:
    def __init__(
        self,
        database_path: Path | str,
        migrations: Iterable[Migration] = DEFAULT_MIGRATIONS,
    ):
        self.database_path = Path(database_path).expanduser().resolve()
        self.migrations = tuple(migrations)
        self._validate_plan()

    def migrate(self) -> int:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, isolation_level=None)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            # One immediate transaction serializes concurrent startup attempts and
            # keeps every pending schema change atomic with its history record.
            connection.execute("BEGIN IMMEDIATE")
            self._ensure_history_table(connection)
            applied = {
                version: (name, checksum)
                for version, name, checksum in connection.execute(
                    "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
                ).fetchall()
            }
            self._validate_history(applied)

            for migration in self.migrations:
                if migration.version in applied:
                    continue
                for statement in migration.statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
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
        expected_versions = tuple(range(1, len(self.migrations) + 1))
        if versions != expected_versions:
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
    def _ensure_history_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY CHECK (version > 0),
                name TEXT NOT NULL UNIQUE,
                checksum TEXT NOT NULL CHECK (length(checksum) = 64),
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
