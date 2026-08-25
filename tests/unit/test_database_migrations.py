from concurrent.futures import ThreadPoolExecutor
import base64
import os
import shutil
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from src.server.application import Application
from src.server.config import ServerConfig
from src.services.database import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_MIGRATIONS,
    Migration,
    SQLiteMigrator,
    SchemaHistoryError,
)
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR


class SQLiteMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.database_path = self.root / "database" / "past-partner.sqlite3"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_creates_a_versioned_database_from_an_empty_directory(self) -> None:
        version = SQLiteMigrator(self.database_path).migrate()

        self.assertEqual(CURRENT_SCHEMA_VERSION, version)
        self.assertTrue(self.database_path.is_file())
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            ).fetchall()
        self.assertEqual(
            [
                (1, "bootstrap_schema"),
                (2, "persona_repository"),
                (3, "import_repository"),
                (4, "local_auth_owner"),
                (5, "media_consent_repository"),
                (6, "training_job_repository"),
                (7, "training_job_revisions"),
                (8, "device_pairing_sessions"),
                (9, "conversation_repository"),
                (10, "task_queue"),
                (11, "session_scopes"),
                (12, "audit_events"),
                (13, "usage_records"),
                (14, "learning_repositories"),
                (15, "anonymous_deletion_receipts"),
                (16, "multi_account_identity"),
                (17, "task_broker_outbox"),
                (18, "worker_observations"),
            ],
            rows,
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('personas', 'training_jobs', 'style_profiles', 'long_term_memories', 'vector_indexes', 'task_broker_outbox', 'worker_observations') ORDER BY name"
            ).fetchall()
        self.assertEqual(
            [
                ("long_term_memories",),
                ("personas",),
                ("style_profiles",),
                ("task_broker_outbox",),
                ("training_jobs",),
                ("vector_indexes",),
                ("worker_observations",),
            ],
            tables,
        )

    def test_repeated_migration_is_idempotent(self) -> None:
        migrator = SQLiteMigrator(self.database_path)

        first_version = migrator.migrate()
        second_version = migrator.migrate()

        self.assertEqual(first_version, second_version)
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(CURRENT_SCHEMA_VERSION, count)

    def test_upgrades_a_version_one_database_to_persona_repository(self) -> None:
        SQLiteMigrator(
            self.database_path,
            (Migration(version=1, name="bootstrap_schema", statements=()),),
        ).migrate()

        self.assertEqual(CURRENT_SCHEMA_VERSION, SQLiteMigrator(self.database_path).migrate())
        with closing(sqlite3.connect(self.database_path)) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'personas'"
            ).fetchone()
        self.assertEqual(("personas",), table)

    def test_upgrades_a_version_two_database_to_import_repository(self) -> None:
        SQLiteMigrator(
            self.database_path,
            DEFAULT_MIGRATIONS[:2],
        ).migrate()

        self.assertEqual(CURRENT_SCHEMA_VERSION, SQLiteMigrator(self.database_path).migrate())
        with closing(sqlite3.connect(self.database_path)) as connection:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('imports', 'import_manifests') ORDER BY name"
            ).fetchall()
        self.assertEqual([("import_manifests",), ("imports",)], tables)

    def test_upgrades_version_seven_without_rewriting_existing_loopback_sessions(self) -> None:
        SQLiteMigrator(self.database_path, DEFAULT_MIGRATIONS[:7]).migrate()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO local_users (id, kind, record_version, encrypted_payload) "
                "VALUES ('owner-1', 'owner', 1, X'01')"
            )
            connection.execute(
                "INSERT INTO local_sessions (token_hash, user_id, expires_at) "
                "VALUES (X'0101010101010101010101010101010101010101010101010101010101010101', "
                "'owner-1', '2099-01-01T00:00:00+00:00')"
            )
            connection.commit()

        self.assertEqual(CURRENT_SCHEMA_VERSION, SQLiteMigrator(self.database_path).migrate())
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT user_id, expires_at, session_origin, pairing_token_fingerprint, scopes "
                "FROM local_sessions"
            ).fetchone()
        self.assertEqual(
            ("owner-1", "2099-01-01T00:00:00+00:00", "loopback", None, "owner:read,owner:write"),
            row,
        )

    def test_failed_persona_migration_rolls_back_table_and_version(self) -> None:
        version_one = Migration(version=1, name="bootstrap_schema", statements=())
        SQLiteMigrator(self.database_path, (version_one,)).migrate()
        broken_persona_migration = Migration(
            version=2,
            name="persona_repository",
            statements=(
                "CREATE TABLE personas (id TEXT PRIMARY KEY)",
                "THIS IS NOT VALID SQL",
            ),
        )

        with self.assertRaises(sqlite3.DatabaseError):
            SQLiteMigrator(self.database_path, (version_one, broken_persona_migration)).migrate()

        with closing(sqlite3.connect(self.database_path)) as connection:
            applied_versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'personas'"
            ).fetchone()
        self.assertEqual([(1,)], applied_versions)
        self.assertIsNone(table)

    def test_concurrent_migration_is_idempotent(self) -> None:
        def migrate() -> int:
            return SQLiteMigrator(self.database_path).migrate()

        with ThreadPoolExecutor(max_workers=2) as executor:
            versions = sorted(executor.map(lambda _: migrate(), range(2)))

        self.assertEqual([CURRENT_SCHEMA_VERSION, CURRENT_SCHEMA_VERSION], versions)
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(CURRENT_SCHEMA_VERSION, count)

    def test_failed_pending_migration_rolls_back_its_schema_and_version(self) -> None:
        SQLiteMigrator(self.database_path).migrate()
        broken_plan = DEFAULT_MIGRATIONS + (
            Migration(
                version=CURRENT_SCHEMA_VERSION + 1,
                name="broken_migration",
                statements=(
                    "CREATE TABLE should_be_rolled_back (id INTEGER PRIMARY KEY)",
                    "THIS IS NOT VALID SQL",
                ),
            ),
        )

        with self.assertRaises(sqlite3.DatabaseError):
            SQLiteMigrator(self.database_path, broken_plan).migrate()

        with closing(sqlite3.connect(self.database_path)) as connection:
            applied_versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            leaked_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'should_be_rolled_back'"
            ).fetchone()
            self.assertEqual(
                [(version,) for version in range(1, CURRENT_SCHEMA_VERSION + 1)],
                applied_versions,
            )
        self.assertIsNone(leaked_table)

    def test_rejects_changed_history_for_an_applied_version(self) -> None:
        SQLiteMigrator(self.database_path).migrate()
        changed_plan = (Migration(version=1, name="renamed_bootstrap", statements=()),)

        with self.assertRaises(SchemaHistoryError):
            SQLiteMigrator(self.database_path, changed_plan).migrate()

    def test_rejects_changed_sql_for_an_applied_version(self) -> None:
        SQLiteMigrator(self.database_path).migrate()
        changed_plan = (
            Migration(
                version=1,
                name="bootstrap_schema",
                statements=("CREATE TABLE unexpected_change (id INTEGER PRIMARY KEY)",),
            ),
        )

        with self.assertRaises(SchemaHistoryError):
            SQLiteMigrator(self.database_path, changed_plan).migrate()

    def test_rejects_an_empty_migration_plan(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one migration"):
            SQLiteMigrator(self.database_path, ())

    def test_application_startup_runs_migrations_at_server_owned_path(self) -> None:
        config = ServerConfig(data_dir=self.root, web_dir=Path.cwd() / "web", mode="test")

        key = base64.b64encode(b"m" * MASTER_KEY_BYTES).decode("ascii")
        with patch.dict(os.environ, {MASTER_KEY_ENV_VAR: key}):
            Application.from_config(config)
            Application.from_config(config)

        self.assertTrue(self.database_path.is_file())
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(CURRENT_SCHEMA_VERSION, count)

    def test_multi_account_identity_schema_preserves_existing_owner_rows(self) -> None:
        SQLiteMigrator(self.database_path, DEFAULT_MIGRATIONS[:15]).migrate()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "INSERT INTO local_users (id, kind, record_version, encrypted_payload) "
                "VALUES ('owner-1', 'owner', 1, X'01')"
            )
            connection.execute(
                "INSERT INTO personas (id, owner_id, record_version, encrypted_payload) "
                "VALUES ('persona-1', 'owner-1', 1, X'01')"
            )
            connection.commit()

        self.assertEqual(CURRENT_SCHEMA_VERSION, SQLiteMigrator(self.database_path).migrate())
        with closing(sqlite3.connect(self.database_path)) as connection:
            user = connection.execute(
                "SELECT id, kind FROM local_users WHERE id = 'owner-1'"
            ).fetchone()
            persona = connection.execute(
                "SELECT owner_id FROM personas WHERE id = 'persona-1'"
            ).fetchone()
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(local_identities)").fetchall()
            }
        self.assertEqual(("owner-1", "owner"), user)
        self.assertEqual(("owner-1",), persona)
        self.assertEqual(
            {"user_id", "tenant_id", "subject", "role", "created_at"},
            columns,
        )


if __name__ == "__main__":
    unittest.main()
