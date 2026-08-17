from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.services.metadata_store import MetadataConnection, MetadataStore, MetadataStoreError
from src.services.sqlite_metadata_store import SQLiteMetadataStore


class MetadataStoreContractTests(unittest.TestCase):
    def test_sqlite_adapter_exposes_store_and_connection_contracts(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            store = SQLiteMetadataStore(Path(directory) / "metadata.sqlite3")

            self.assertIsInstance(store, MetadataStore)
            version = store.migrate()
            self.assertGreaterEqual(version, 1)
            connection = store.connect()
            try:
                self.assertIsInstance(connection, MetadataConnection)
                self.assertEqual(1, connection.execute("PRAGMA foreign_keys").fetchone()[0])
            finally:
                connection.close()

    def test_transaction_commits_and_rolls_back_explicitly(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            store = SQLiteMetadataStore(Path(directory) / "metadata.sqlite3")
            store.migrate()

            with store.transaction(immediate=True) as connection:
                connection.execute(
                    "CREATE TABLE contract_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute("INSERT INTO contract_probe(value) VALUES (?)", ("ok",))

            with self.assertRaises(RuntimeError):
                with store.transaction() as connection:
                    connection.execute("INSERT INTO contract_probe(value) VALUES (?)", ("rolled-back",))
                    raise RuntimeError("abort")

            with closing(store.connect()) as connection:
                values = connection.execute("SELECT value FROM contract_probe").fetchall()
            self.assertEqual([("ok",)], values)

    def test_adapter_errors_have_stable_code_without_path(self) -> None:
        missing = Path.cwd() / "metadata-store-missing" / "db.sqlite3"
        store = SQLiteMetadataStore(missing)
        store._connect_impl = lambda: (_ for _ in ()).throw(sqlite3.OperationalError("secret/path"))

        with self.assertRaises(MetadataStoreError) as captured:
            store.connect()

        self.assertEqual("metadata_connection_failed", captured.exception.code)
        self.assertNotIn("secret/path", str(captured.exception))
        self.assertNotIn(str(missing), str(captured.exception))


if __name__ == "__main__":
    unittest.main()
