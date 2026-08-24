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
    requires_foreign_keys_off: bool = False

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
# and cascade deletion. Version 10 adds an owner-scoped durable task queue. Routing
# and lease metadata stay queryable; payloads and results remain encrypted blobs.
# Version 11 persists the canonical owner read/write scope set on each session. Existing
# sessions default to the full local-owner set so this additive migration preserves the
# single-owner development behavior while allowing future account boundaries to narrow it.
# Version 12 adds encrypted owner-scoped business audit events. Only bounded routing
# fields remain queryable; the event payload itself is encrypted by AuditRepository.
# Version 13 adds encrypted owner-scoped usage records. Version 14 adds encrypted
# style profiles, reviewed long-term memory aggregates, and deterministic vector
# index envelopes scoped to the owning persona.
# Version 15 adds an owner-free deletion receipt containing only a random receipt ID,
# timestamp, and bounded resource counts.
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
    Migration(
        version=10,
        name="task_queue",
        statements=(
            """
            CREATE TABLE task_queue (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                task_type TEXT NOT NULL CHECK (length(task_type) BETWEEN 1 AND 128),
                state TEXT NOT NULL CHECK (state IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                max_attempts INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 20),
                available_at TEXT NOT NULL,
                leased_until TEXT,
                lease_owner TEXT,
                failure_code TEXT,
                retryable INTEGER NOT NULL DEFAULT 0 CHECK (retryable IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0),
                CHECK ((state = 'leased' AND lease_owner IS NOT NULL AND leased_until IS NOT NULL)
                    OR (state <> 'leased' AND lease_owner IS NULL AND leased_until IS NULL)),
                CHECK ((state = 'failed' AND failure_code IS NOT NULL)
                    OR (state <> 'failed'))
            )
            """,
            "CREATE INDEX task_queue_claim_idx ON task_queue(state, available_at, leased_until)",
            "CREATE INDEX task_queue_owner_idx ON task_queue(owner_id, created_at, id)",
        ),
    ),
    Migration(
        version=11,
        name="session_scopes",
        statements=(
            "ALTER TABLE local_sessions ADD COLUMN scopes TEXT NOT NULL "
            "DEFAULT 'owner:read,owner:write' "
            "CHECK (scopes IN ('owner:read', 'owner:write', 'owner:read,owner:write'))",
        ),
    ),
    Migration(
        version=12,
        name="audit_events",
        statements=(
            """
            CREATE TABLE audit_events (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                action TEXT NOT NULL CHECK (length(action) BETWEEN 1 AND 64),
                outcome TEXT NOT NULL CHECK (outcome = 'success'),
                resource_type TEXT NOT NULL CHECK (length(resource_type) BETWEEN 1 AND 64),
                resource_id TEXT NOT NULL CHECK (length(resource_id) BETWEEN 1 AND 128),
                occurred_at TEXT NOT NULL,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
            "CREATE INDEX audit_events_owner_cursor_idx ON audit_events(owner_id, occurred_at, id)",
            "CREATE INDEX audit_events_owner_action_idx ON audit_events(owner_id, action, occurred_at)",
        ),
    ),
    Migration(
        version=13,
        name="usage_records",
        statements=(
            """
            CREATE TABLE usage_records (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                operation TEXT NOT NULL CHECK (operation IN ('chat')),
                provider_id TEXT NOT NULL CHECK (length(provider_id) BETWEEN 1 AND 128),
                model_id TEXT NOT NULL CHECK (length(model_id) BETWEEN 1 AND 256),
                billing_mode TEXT NOT NULL CHECK (billing_mode IN ('platform_billed', 'provider_billed', 'local_compute')),
                charge_state TEXT NOT NULL CHECK (charge_state IN ('priced', 'usage_unavailable', 'pricing_unavailable')),
                occurred_at TEXT NOT NULL,
                provider_request_fingerprint TEXT,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0),
                CHECK (provider_request_fingerprint IS NULL OR length(provider_request_fingerprint) = 64)
            )
            """,
            "CREATE INDEX usage_records_owner_cursor_idx ON usage_records(owner_id, occurred_at, id)",
            "CREATE UNIQUE INDEX usage_records_owner_request_idx ON usage_records(owner_id, provider_id, provider_request_fingerprint) WHERE provider_request_fingerprint IS NOT NULL",
        ),
    ),
    Migration(
        version=14,
        name="learning_repositories",
        statements=(
            """
            CREATE TABLE style_profiles (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0),
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, persona_id)
            )
            """,
            "CREATE INDEX style_profiles_owner_persona_idx ON style_profiles(owner_id, persona_id)",
            """
            CREATE TABLE long_term_memories (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0),
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, persona_id)
            )
            """,
            "CREATE INDEX long_term_memories_owner_persona_idx ON long_term_memories(owner_id, persona_id)",
            """
            CREATE TABLE vector_indexes (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                index_version INTEGER NOT NULL CHECK (index_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0),
                updated_at TEXT NOT NULL,
                UNIQUE(owner_id, persona_id)
            )
            """,
            "CREATE INDEX vector_indexes_owner_persona_idx ON vector_indexes(owner_id, persona_id)",
        ),
    ),
    Migration(
        version=15,
        name="anonymous_deletion_receipts",
        statements=(
            """
            CREATE TABLE deletion_receipts (
                id TEXT PRIMARY KEY,
                deleted_at TEXT NOT NULL,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                counts_json TEXT NOT NULL CHECK (length(counts_json) BETWEEN 2 AND 4096)
            )
            """,
        ),
    ),
    Migration(
        version=16,
        name="multi_account_identity",
        requires_foreign_keys_off=True,
        statements=(
            # SQLite cannot drop a CHECK constraint in place. Rebuilding this
            # small root table while foreign-key enforcement is temporarily
            # disabled preserves every existing child row and lets the new
            # member kind coexist with the legacy owner record.
            """
            CREATE TABLE local_users_r1_03 (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL CHECK (kind IN ('owner', 'member')),
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
            "INSERT INTO local_users_r1_03 (id, kind, record_version, encrypted_payload) "
            "SELECT id, kind, record_version, encrypted_payload FROM local_users",
            "DROP TABLE local_users",
            "ALTER TABLE local_users_r1_03 RENAME TO local_users",
            """
            CREATE TABLE local_identities (
                user_id TEXT PRIMARY KEY REFERENCES local_users(id) ON DELETE CASCADE,
                tenant_id TEXT NOT NULL CHECK (length(tenant_id) BETWEEN 1 AND 128),
                subject TEXT NOT NULL UNIQUE CHECK (length(subject) BETWEEN 1 AND 256),
                role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX local_identities_tenant_idx ON local_identities(tenant_id, user_id)",
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
        foreign_keys_off = False
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
                if migration.requires_foreign_keys_off and not foreign_keys_off:
                    # PRAGMA foreign_keys is a connection setting and is a
                    # no-op inside a transaction. Commit the preceding
                    # migrations, toggle it outside the transaction, and
                    # start a new atomic unit for the table rebuild.
                    connection.commit()
                    connection.execute("PRAGMA foreign_keys = OFF")
                    foreign_keys_off = True
                    connection.execute("BEGIN IMMEDIATE")
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
            if foreign_keys_off:
                connection.execute("PRAGMA foreign_keys = ON")
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
