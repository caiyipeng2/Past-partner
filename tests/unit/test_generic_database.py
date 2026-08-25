import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.preprocessing.generic_database import SnapshotChangedError, create_generic_snapshot
from src.preprocessing.parser_registry import ParserError, ParserRegistry


class GenericDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ParserRegistry.with_builtins()

    def test_auto_detects_common_message_schema_in_generic_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "conversation-db"
            self._create_messages_fixture(root)
            source_bytes = {path: path.read_bytes() for path in root.rglob("*.db")}

            result = self.registry.parse(root)

            self.assertEqual(source_bytes, {path: path.read_bytes() for path in root.rglob("*.db")})

        self.assertEqual("generic_sqlite", result.source_type)
        self.assertEqual(["你好", "收到文件"], [item.content for item in result.records])
        self.assertEqual("user-2", result.records[0].sender_id)
        self.assertEqual("小雨", result.records[0].sender_name)
        self.assertEqual("file", result.records[1].message_type)
        self.assertEqual("generic_messages", result.summary["schema"])
        self.assertTrue(all(item.record_id for item in result.records))

    def test_limits_generic_database_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "conversation-db"
            self._create_messages_fixture(root)

            result = self.registry.parse(root, max_records=1)

        self.assertEqual("generic_sqlite", result.source_type)
        self.assertEqual(1, len(result.records))
        self.assertTrue(result.summary["truncated"])

    def test_extracts_attachment_reference_columns_from_generic_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "conversation-db"
            self._create_attachment_fixture(root)

            result = self.registry.parse(root)

        self.assertEqual("image", result.records[0].attachments[0]["kind"])
        self.assertEqual("images/photo.jpg", result.records[0].attachments[0]["path"])
        self.assertEqual(512, result.records[0].attachments[0]["size"])

    def test_rejects_unknown_generic_database_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "conversation-db"
            root.mkdir()
            connection = sqlite3.connect(root / "profile.db")
            with connection:
                connection.execute("CREATE TABLE profile (id INTEGER, nickname TEXT)")
            connection.close()

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(root, {"source_type": "generic_sqlite"})

        self.assertEqual("unsupported_schema", raised.exception.code)
        self.assertNotIn("sqlite3", str(raised.exception).lower())

    def test_rejects_non_sqlite_generic_database_without_guessing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "conversation-db"
            root.mkdir()
            (root / "messages.db").write_bytes(b"encrypted database")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(root, {"source_type": "generic_sqlite"})

        self.assertEqual("encrypted_database", raised.exception.code)
        self.assertIn("密钥", str(raised.exception))

    def test_rejects_single_database_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.db"
            path.write_bytes(b"SQLite format 3\x00")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path, {"source_type": "generic_sqlite"})

        self.assertEqual("source_not_directory", raised.exception.code)

    def test_snapshot_copies_sidecars_and_retries_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "conversation-db"
            source.mkdir()
            database = source / "messages.db"
            database.write_bytes(b"SQLite format 3\x00fixture")
            (source / "messages.db-wal").write_bytes(b"wal")
            (source / "messages.db-shm").write_bytes(b"shm")
            cache = Path(directory) / "cache"

            snapshot = create_generic_snapshot(source, cache)
            self.assertEqual(
                {"messages.db", "messages.db-wal", "messages.db-shm"},
                {item.copied.relative_to(snapshot.root).as_posix() for item in snapshot.files},
            )

            calls = 0

            def mutate_after_copy(src: Path, dst: Path) -> None:
                nonlocal calls
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
                calls += 1
                if calls == 1:
                    previous_mtime = database.stat().st_mtime_ns
                    database.write_bytes(b"SQLite format 3\x00changed")
                    os.utime(database, ns=(previous_mtime, previous_mtime + 1_000_000))

            with self.assertRaises(SnapshotChangedError):
                create_generic_snapshot(source, cache, retries=1, copy_file=mutate_after_copy)

    @staticmethod
    def _create_messages_fixture(root: Path) -> None:
        root.mkdir(parents=True)
        connection = sqlite3.connect(root / "messages.db")
        with connection:
            connection.executescript(
                """
                CREATE TABLE chat_log (
                    event_id INTEGER, conversation_id TEXT, author_id TEXT,
                    author_name TEXT, body TEXT, sent_at INTEGER, kind INTEGER
                );
                INSERT INTO chat_log VALUES
                    (1, 'conversation-1', 'user-2', '小雨', '你好', 1780000000, 1);
                INSERT INTO chat_log VALUES
                    (2, 'conversation-1', 'user-1', '我', '收到文件', 1780000060, 5);
                """
            )
        connection.close()

    @staticmethod
    def _create_attachment_fixture(root: Path) -> None:
        root.mkdir(parents=True)
        connection = sqlite3.connect(root / "messages.db")
        with connection:
            connection.executescript(
                """
                CREATE TABLE chat_log (
                    event_id INTEGER, sender_id TEXT, sender_name TEXT, content TEXT,
                    timestamp INTEGER, message_type TEXT, attachment_path TEXT,
                    attachment_size INTEGER, attachment_type TEXT
                );
                INSERT INTO chat_log VALUES
                    (1, 'user-1', '小雨', '看图', 1780000000, 'image',
                     'images/photo.jpg', 512, 'image/jpeg');
                """
            )
        connection.close()


if __name__ == "__main__":
    unittest.main()
