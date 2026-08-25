from __future__ import annotations

import unittest

from src.services.database import DEFAULT_MIGRATIONS, SchemaHistoryError
from src.services.postgresql_database import PostgreSQLMigrator


class _Result:
    def __init__(self, rows: list[tuple[object, ...]] = []) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return list(self._rows)


class _MigrationConnection:
    def __init__(self, applied: tuple[tuple[int, str, str], ...] = ()) -> None:
        self.applied = {version: (name, checksum) for version, name, checksum in applied}
        self.calls: list[tuple[str, object]] = []
        self.in_transaction = False
        self.commit_count = 0
        self.rollback_count = 0
        self.close_count = 0
        self.fail_on: str | None = None

    def execute(self, sql: str, parameters: object = ()) -> _Result:
        self.calls.append((sql, parameters))
        upper_sql = sql.strip().upper()
        if self.fail_on and self.fail_on in upper_sql:
            raise RuntimeError("driver secret must not affect transaction cleanup")
        if upper_sql.startswith("BEGIN"):
            self.in_transaction = True
        elif upper_sql.startswith("SELECT VERSION"):
            return _Result(
                [
                    (version, name, checksum)
                    for version, (name, checksum) in sorted(self.applied.items())
                ]
            )
        elif upper_sql.startswith("INSERT INTO SCHEMA_MIGRATIONS"):
            version, name, checksum = parameters  # type: ignore[misc]
            self.applied[int(version)] = (str(name), str(checksum))
        return _Result()

    def commit(self) -> None:
        self.commit_count += 1
        self.in_transaction = False

    def rollback(self) -> None:
        self.rollback_count += 1
        self.in_transaction = False

    def close(self) -> None:
        self.close_count += 1


class PostgreSQLMigrationTests(unittest.TestCase):
    def test_migrates_all_logical_versions_with_postgresql_types(self) -> None:
        connection = _MigrationConnection()
        migrator = PostgreSQLMigrator(lambda: connection)

        expected_version = len(DEFAULT_MIGRATIONS)
        self.assertEqual(expected_version, migrator.migrate())
        self.assertEqual(tuple(range(1, expected_version + 1)), tuple(sorted(connection.applied)))
        self.assertEqual(1, connection.commit_count)
        self.assertEqual(0, connection.rollback_count)
        self.assertEqual(1, connection.close_count)

        sql_statements = [sql for sql, _ in connection.calls]
        ledger_ddl = next(sql for sql in sql_statements if "CREATE TABLE IF NOT EXISTS schema_migrations" in sql)
        self.assertIn("TIMESTAMPTZ", ledger_ddl.upper())
        migration_ddl = [sql for sql in sql_statements if "BLOB" in sql.upper() or "BYTEA" in sql.upper()]
        self.assertTrue(migration_ddl)
        self.assertTrue(all("BLOB" not in sql.upper() for sql in migration_ddl))
        self.assertTrue(any("BYTEA" in sql.upper() for sql in migration_ddl))
        self.assertTrue(any("DROP CONSTRAINT IF EXISTS LOCAL_USERS_KIND_KEY" in sql.upper() for sql in sql_statements))
        self.assertFalse(any("DROP TABLE LOCAL_USERS" in sql.upper() for sql in sql_statements))

        recorded = {version: checksum for version, (_, checksum) in connection.applied.items()}
        self.assertEqual(
            {migration.version: migration.checksum for migration in DEFAULT_MIGRATIONS},
            recorded,
        )

    def test_repeated_migration_is_idempotent(self) -> None:
        connection = _MigrationConnection()
        migrator = PostgreSQLMigrator(lambda: connection)

        migrator.migrate()
        first_call_count = len(connection.calls)
        expected_version = len(DEFAULT_MIGRATIONS)
        self.assertEqual(expected_version, migrator.migrate())

        self.assertEqual(2, connection.commit_count)
        self.assertEqual(expected_version, len(connection.applied))
        self.assertEqual(first_call_count + 3, len(connection.calls))

    def test_checksum_mismatch_fails_closed_and_rolls_back(self) -> None:
        connection = _MigrationConnection(((1, "bootstrap_schema", "0" * 64),))
        migrator = PostgreSQLMigrator(lambda: connection)

        with self.assertRaises(SchemaHistoryError):
            migrator.migrate()

        self.assertEqual(0, connection.commit_count)
        self.assertEqual(1, connection.rollback_count)
        self.assertEqual(1, connection.close_count)

    def test_failed_statement_rolls_back_without_commit(self) -> None:
        connection = _MigrationConnection()
        connection.fail_on = "CREATE TABLE TRAINING_JOBS"
        migrator = PostgreSQLMigrator(lambda: connection)

        with self.assertRaises(RuntimeError):
            migrator.migrate()

        self.assertEqual(0, connection.commit_count)
        self.assertEqual(1, connection.rollback_count)
        self.assertEqual(1, connection.close_count)


if __name__ == "__main__":
    unittest.main()
