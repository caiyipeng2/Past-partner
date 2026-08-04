from concurrent.futures import ThreadPoolExecutor
import shutil
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.services.database import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_MIGRATIONS,
    Migration,
    SQLiteMigrator,
    SchemaHistoryError,
)


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
            [(1, "bootstrap_schema"), (2, "persona_repository")],
            rows,
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'personas'"
            ).fetchone()
        self.assertEqual(("personas",), table)

    def test_repeated_migration_is_idempotent(self) -> None:
        migrator = SQLiteMigrator(self.database_path)

        first_version = migrator.migrate()
        second_version = migrator.migrate()

        self.assertEqual(first_version, second_version)
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(2, count)

    def test_upgrades_a_version_one_database_to_persona_repository(self) -> None:
        SQLiteMigrator(
            self.database_path,
            (Migration(version=1, name="bootstrap_schema", statements=()),),
        ).migrate()

        self.assertEqual(2, SQLiteMigrator(self.database_path).migrate())
        with closing(sqlite3.connect(self.database_path)) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'personas'"
            ).fetchone()
        self.assertEqual(("personas",), table)

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

        self.assertEqual([2, 2], versions)
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(2, count)

    def test_failed_pending_migration_rolls_back_its_schema_and_version(self) -> None:
        SQLiteMigrator(self.database_path).migrate()
        broken_plan = DEFAULT_MIGRATIONS + (
            Migration(
                version=3,
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
            self.assertEqual([(1,), (2,)], applied_versions)
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

        Application.from_config(config)
        Application.from_config(config)

        self.assertTrue(self.database_path.is_file())
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        self.assertEqual(2, count)


if __name__ == "__main__":
    unittest.main()
