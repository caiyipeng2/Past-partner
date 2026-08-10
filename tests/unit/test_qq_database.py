import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.preprocessing.parser_registry import ParserError, ParserRegistry
from src.preprocessing.qq_database import SnapshotChangedError, create_qq_snapshot


class QqDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ParserRegistry.with_builtins()

    def test_parses_plaintext_qq_database_directory_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "qq_db"
            self._create_messages_fixture(root)
            source_bytes = {path: path.read_bytes() for path in root.rglob("*.db")}

            result = self.registry.parse(
                root,
                {"source_type": "qq_database", "self_id": "10001"},
            )

            self.assertEqual(source_bytes, {path: path.read_bytes() for path in root.rglob("*.db")})

        self.assertEqual("qq_database", result.source_type)
        self.assertEqual(["你好", "收到图片"], [item.content for item in result.records])
        self.assertEqual("10002", result.records[0].sender_id)
        self.assertEqual("小雨", result.records[0].sender_name)
        self.assertEqual("image", result.records[1].message_type)
        self.assertTrue(all(item.record_id for item in result.records))
        self.assertEqual("generic_messages", result.summary["schema"])

    def test_supports_common_qq_export_column_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "QQ聊天数据库"
            root.mkdir()
            connection = sqlite3.connect(root / "Message.db")
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE Message (
                        msgId INTEGER, uin TEXT, nickName TEXT, body TEXT,
                        createdAt INTEGER, msgType INTEGER
                    );
                    INSERT INTO Message VALUES (11, '20002', '阿明', '别忘了带伞', 1780000000, 1);
                    """
                )
            connection.close()

            result = self.registry.parse(root)

        self.assertEqual("qq_database", result.source_type)
        self.assertEqual("20002", result.records[0].sender_id)
        self.assertEqual("阿明", result.records[0].sender_name)
        self.assertEqual("别忘了带伞", result.records[0].content)

    def test_parses_common_sqlite_database_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "QQData"
            root.mkdir()
            connection = sqlite3.connect(root / "messages.sqlite3")
            with connection:
                connection.execute(
                    "CREATE TABLE messages (sender_id TEXT, content TEXT, timestamp INTEGER)"
                )
                connection.execute(
                    "INSERT INTO messages VALUES (?, ?, ?)",
                    ("30001", "sqlite3 扩展名", 1780000000),
                )
            connection.close()

            result = self.registry.parse(root)

        self.assertEqual("qq_database", result.source_type)
        self.assertEqual("sqlite3 扩展名", result.records[0].content)

    def test_rejects_single_database_file_even_without_explicit_source_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "QQMessage.db"
            path.write_bytes(b"SQLite format 3\x00")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("source_not_directory", raised.exception.code)
        self.assertIn("目录", str(raised.exception))

    def test_rejects_unknown_schema_with_stable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "qq"
            root.mkdir()
            connection = sqlite3.connect(root / "profile.db")
            with connection:
                connection.execute("CREATE TABLE profile (uin TEXT, nickname TEXT)")
            connection.close()

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(root, {"source_type": "qq_database"})

        self.assertEqual("unsupported_schema", raised.exception.code)
        self.assertNotIn("sqlite3", str(raised.exception).lower())

    def test_rejects_encrypted_database_without_guessing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "qq"
            root.mkdir()
            (root / "Message.db").write_bytes(b"\x01\x02encrypted")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(root, {"source_type": "qq_database"})

        self.assertEqual("encrypted_database", raised.exception.code)
        self.assertIn("密钥", str(raised.exception))

    def test_reports_corrupt_sqlite_with_stable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "qq"
            root.mkdir()
            (root / "Message.db").write_bytes(b"SQLite format 3\x00corrupt")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(root, {"source_type": "qq_database"})

        self.assertEqual("corrupt_database", raised.exception.code)
        self.assertNotIn("sqlite3", str(raised.exception).lower())

    def test_snapshot_copies_wal_and_shm_and_rejects_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "qq"
            source.mkdir()
            database = source / "Message.db"
            database.write_bytes(b"SQLite format 3\x00fixture")
            (source / "Message.db-wal").write_bytes(b"wal")
            (source / "Message.db-shm").write_bytes(b"shm")
            cache = Path(directory) / "cache"
            snapshot = create_qq_snapshot(source, cache)

            self.assertEqual(
                {"Message.db", "Message.db-wal", "Message.db-shm"},
                {item.copied.relative_to(snapshot.root).as_posix() for item in snapshot.files},
            )

            calls = 0

            def mutate_after_copy(src: Path, dst: Path) -> None:
                nonlocal calls
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
                calls += 1
                if calls == 1:
                    database.write_bytes(b"SQLite format 3\x00changed")

            with self.assertRaises(SnapshotChangedError):
                create_qq_snapshot(source, cache, retries=1, copy_file=mutate_after_copy)

    def test_snapshot_includes_sidecars_for_sqlite3_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "qq"
            source.mkdir()
            database = source / "messages.sqlite3"
            database.write_bytes(b"SQLite format 3\x00fixture")
            (source / "messages.sqlite3-wal").write_bytes(b"wal")
            (source / "messages.sqlite3-shm").write_bytes(b"shm")

            snapshot = create_qq_snapshot(source, Path(directory) / "cache")

        self.assertEqual(
            {"messages.sqlite3", "messages.sqlite3-wal", "messages.sqlite3-shm"},
            {item.copied.relative_to(snapshot.root).as_posix() for item in snapshot.files},
        )

    @staticmethod
    def _create_messages_fixture(root: Path) -> None:
        root.mkdir(parents=True)
        connection = sqlite3.connect(root / "messages.db")
        with connection:
            connection.executescript(
                """
                CREATE TABLE messages (
                    id INTEGER, chat_id TEXT, sender_id TEXT, sender_name TEXT,
                    content TEXT, timestamp INTEGER, message_type INTEGER
                );
                INSERT INTO messages VALUES (1, 'group-1', '10002', '小雨', '你好', 1780000000, 1);
                INSERT INTO messages VALUES (2, 'group-1', '10001', '我', '收到图片', 1780000060, 2);
                """
            )
        connection.close()


if __name__ == "__main__":
    unittest.main()
