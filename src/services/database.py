"""Versioned SQLite schema initialization for local persistence."""

from __future__ import annotations

import sqlite3
from hashlib import sha256
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from src.services.audit_chain import GENESIS_HASH, event_hash as calculate_audit_event_hash


class SchemaHistoryError(RuntimeError):
    """Raised when an existing database no longer matches migration history."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]
    requires_foreign_keys_off: bool = False
    postgres_statements: tuple[str, ...] | None = None
    post_apply: Callable[[object], None] | None = None

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


def _backfill_audit_chain(connection: object) -> None:
    """Anchor pre-existing v12 audit rows while the migration is atomic."""

    rows = connection.execute(
        "SELECT id, owner_id, action, outcome, resource_type, resource_id, occurred_at, "
        "record_version, encrypted_payload FROM audit_events ORDER BY owner_id, occurred_at, id"
    ).fetchall()
    owner_id: str | None = None
    sequence = 0
    previous_hash = GENESIS_HASH
    for row in rows:
        (
            event_id,
            row_owner_id,
            action,
            outcome,
            resource_type,
            resource_id,
            occurred_at,
            record_version,
            encrypted_payload,
        ) = tuple(row)
        if row_owner_id != owner_id:
            owner_id = row_owner_id
            sequence = 0
            previous_hash = GENESIS_HASH
        sequence += 1
        current_hash = calculate_audit_event_hash(
            previous_hash=previous_hash,
            event_id=event_id,
            owner_id=row_owner_id,
            action=action,
            outcome=outcome,
            resource_type=resource_type,
            resource_id=resource_id,
            occurred_at=occurred_at,
            record_version=record_version,
            encrypted_payload=encrypted_payload,
        )
        connection.execute(
            "UPDATE audit_events SET chain_sequence = ?, previous_hash = ?, event_hash = ? WHERE id = ?",
            (sequence, previous_hash, current_hash, event_id),
        )
        previous_hash = current_hash


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
# Version 16 adds local account identity mappings while preserving the legacy owner row.
# Version 17 adds a redacted task notification outbox. It contains no owner, payload,
# provider, credential, or filesystem fields, and is written with each task enqueue.
# Version 18 adds bounded, redacted worker lifecycle observations. These rows are
# operational metadata only: they contain no owner, task payload, provider response,
# exception text, token, or filesystem path.
# Version 19 adds encrypted owner-scoped billing entries. Monetary values are stored
# as positive integer minor units with a direction column; operation keys make future
# payment/usage adapters idempotent without exposing payment payloads to the client.
# Version 20 adds encrypted owner subscription snapshots and provider event records.
# Only the provider event hash and routing metadata remain queryable; event payloads
# and provider identifiers that need confidentiality stay inside encrypted envelopes.
# Version 21 adds per-owner audit chain metadata and atomically anchors existing v12
# rows so the verification command can distinguish gaps from hash mismatches.
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
        postgres_statements=(
            "ALTER TABLE local_users DROP CONSTRAINT IF EXISTS local_users_kind_key",
            "ALTER TABLE local_users DROP CONSTRAINT IF EXISTS local_users_kind_check",
            "ALTER TABLE local_users ADD CONSTRAINT local_users_kind_check "
            "CHECK (kind IN ('owner', 'member'))",
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
    Migration(
        version=17,
        name="task_broker_outbox",
        statements=(
            """
            CREATE TABLE task_broker_outbox (
                id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL UNIQUE REFERENCES task_queue(id) ON DELETE CASCADE,
                task_type TEXT NOT NULL CHECK (length(task_type) BETWEEN 1 AND 128),
                created_at TEXT NOT NULL,
                available_at TEXT NOT NULL,
                published_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                last_error_code TEXT,
                CHECK (published_at IS NULL OR length(published_at) > 0),
                CHECK (last_error_code IS NULL OR length(last_error_code) BETWEEN 1 AND 128)
            )
            """,
            "CREATE INDEX task_broker_outbox_pending_idx "
            "ON task_broker_outbox(published_at, available_at, created_at, id)",
        ),
    ),
    Migration(
        version=18,
        name="worker_observations",
        statements=(
            """
            CREATE TABLE worker_observations (
                id TEXT PRIMARY KEY,
                worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 128),
                task_type TEXT NOT NULL CHECK (length(task_type) BETWEEN 1 AND 128),
                outcome TEXT NOT NULL CHECK (outcome IN (
                    'idle', 'succeeded', 'retryable_failure', 'terminal_failure', 'lease_lost'
                )),
                observed_at TEXT NOT NULL,
                duration_ms INTEGER NOT NULL CHECK (duration_ms BETWEEN 0 AND 3600000),
                failure_code TEXT,
                CHECK (failure_code IS NULL OR length(failure_code) BETWEEN 1 AND 128),
                CHECK ((outcome IN ('retryable_failure', 'terminal_failure', 'lease_lost')
                        AND failure_code IS NOT NULL)
                    OR (outcome IN ('idle', 'succeeded') AND failure_code IS NULL))
            )
            """,
            "CREATE INDEX worker_observations_worker_cursor_idx "
            "ON worker_observations(worker_id, observed_at DESC, id DESC)",
            "CREATE INDEX worker_observations_recent_idx "
            "ON worker_observations(observed_at, worker_id, task_type)",
        ),
    ),
    Migration(
        version=19,
        name="billing_entries",
        statements=(
            """
            CREATE TABLE billing_entries (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                direction TEXT NOT NULL CHECK (direction IN ('credit', 'debit')),
                currency TEXT NOT NULL CHECK (length(currency) = 3),
                amount_minor BIGINT NOT NULL CHECK (amount_minor BETWEEN 1 AND 1000000000000),
                source TEXT NOT NULL CHECK (source IN ('payment', 'refund', 'usage', 'subscription')),
                operation_key_hash TEXT NOT NULL CHECK (length(operation_key_hash) = 64),
                occurred_at TEXT NOT NULL,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
            """
            CREATE TABLE billing_accounts (
                owner_id TEXT PRIMARY KEY REFERENCES local_users(id) ON DELETE CASCADE,
                currency TEXT NOT NULL CHECK (length(currency) = 3)
            )
            """,
            "CREATE INDEX billing_entries_owner_cursor_idx ON billing_entries(owner_id, occurred_at, id)",
            "CREATE UNIQUE INDEX billing_entries_owner_operation_idx ON billing_entries(owner_id, operation_key_hash)",
        ),
    ),
    Migration(
        version=20,
        name="subscription_entitlements",
        statements=(
            """
            CREATE TABLE subscriptions (
                owner_id TEXT PRIMARY KEY REFERENCES local_users(id) ON DELETE CASCADE,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0),
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE subscription_events (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
                provider_id TEXT NOT NULL CHECK (length(provider_id) BETWEEN 1 AND 128),
                provider_event_key_hash TEXT NOT NULL CHECK (length(provider_event_key_hash) = 64),
                provider_subscription_hash TEXT NOT NULL CHECK (length(provider_subscription_hash) = 64),
                occurred_at TEXT NOT NULL,
                record_version INTEGER NOT NULL CHECK (record_version = 1),
                encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
            )
            """,
            "CREATE TABLE subscription_bindings ("
            "provider_id TEXT NOT NULL CHECK (length(provider_id) BETWEEN 1 AND 128), "
            "provider_subscription_hash TEXT NOT NULL CHECK (length(provider_subscription_hash) = 64), "
            "owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE, "
            "record_version INTEGER NOT NULL CHECK (record_version = 1), "
            "PRIMARY KEY(provider_id, provider_subscription_hash)"
            ")",
            "CREATE UNIQUE INDEX subscription_events_provider_key_idx "
            "ON subscription_events(provider_id, provider_event_key_hash)",
            "CREATE INDEX subscription_bindings_owner_idx "
            "ON subscription_bindings(owner_id)",
            "CREATE INDEX subscription_events_owner_cursor_idx "
            "ON subscription_events(owner_id, occurred_at, id)",
        ),
    ),
    Migration(
        version=21,
        name="audit_chain",
        statements=(
            "ALTER TABLE audit_events ADD COLUMN chain_sequence INTEGER "
            "CHECK (chain_sequence IS NULL OR chain_sequence > 0)",
            "ALTER TABLE audit_events ADD COLUMN previous_hash TEXT "
            "CHECK (previous_hash IS NULL OR length(previous_hash) = 64)",
            "ALTER TABLE audit_events ADD COLUMN event_hash TEXT "
            "CHECK (event_hash IS NULL OR length(event_hash) = 64)",
            "CREATE UNIQUE INDEX audit_events_owner_chain_idx "
            "ON audit_events(owner_id, chain_sequence)",
        ),
        post_apply=_backfill_audit_chain,
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
                if migration.post_apply is not None:
                    migration.post_apply(connection)
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
