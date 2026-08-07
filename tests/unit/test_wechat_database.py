import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.preprocessing.parser_registry import ParserError, ParserRegistry
from src.preprocessing.wechat_database import SnapshotChangedError, create_wechat_snapshot


class WeChatDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ParserRegistry.with_builtins()

    def test_parses_plaintext_wechat_v3_directory_and_keeps_database_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "db_storage"
            self._create_v3_fixture(root)
            source_bytes = {path: path.read_bytes() for path in root.rglob("*.db")}

            result = self.registry.parse(root, {"source_type": "wechat_database", "self_id": "wxid_self"})

            self.assertEqual(source_bytes, {path: path.read_bytes() for path in root.rglob("*.db")})

        self.assertEqual("wechat_database", result.source_type)
        self.assertEqual(["你好", "[unsupported message type: 3/0]"], [item.content for item in result.records])
        self.assertEqual("wxid_friend", result.records[0].sender_id)
        self.assertEqual("小雨", result.records[0].sender_name)
        self.assertEqual("wxid_self", result.records[1].sender_id)
        self.assertTrue(all(item.record_id for item in result.records))

    def test_parses_plaintext_wechat_v4_directory_for_explicit_chat_id(self) -> None:
        chat_id = "wxid_friend"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "db_storage"
            self._create_v4_fixture(root, chat_id)

            result = self.registry.parse(
                root,
                {"source_type": "wechat_database", "chat_id": chat_id, "self_id": "wxid_self"},
            )

        self.assertEqual("wechat_database", result.source_type)
        self.assertEqual(["v4 明文消息"], [item.content for item in result.records])
        self.assertEqual(chat_id, result.records[0].sender_id)
        self.assertEqual("小雨", result.records[0].sender_name)

    def test_rejects_single_database_file_instead_of_treating_it_as_a_chat_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "MicroMsg.db"
            path.write_bytes(b"SQLite format 3\x00")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path, {"source_type": "wechat_database"})

        self.assertEqual("source_not_directory", raised.exception.code)
        self.assertIn("目录", str(raised.exception))

    def test_rejects_encrypted_database_without_automatic_decryption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "db_storage"
            root.mkdir()
            (root / "MicroMsg.db").write_bytes(b"\x01\x02encrypted")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(root, {"source_type": "wechat_database"})

        self.assertEqual("encrypted_database", raised.exception.code)
        self.assertIn("密钥", str(raised.exception))

    def test_reports_corrupt_sqlite_with_a_stable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "db_storage"
            root.mkdir()
            (root / "MicroMsg.db").write_bytes(b"SQLite format 3\x00corrupt")
            (root / "MSG0.db").write_bytes(b"SQLite format 3\x00corrupt")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(root, {"source_type": "wechat_database"})

        self.assertEqual("corrupt_database", raised.exception.code)
        self.assertNotIn("sqlite3", str(raised.exception).lower())

    def test_snapshot_copies_wal_and_shm_and_rejects_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "db_storage"
            source.mkdir()
            database = source / "MSG0.db"
            database.write_bytes(b"SQLite format 3\x00fixture")
            (source / "MSG0.db-wal").write_bytes(b"wal")
            (source / "MSG0.db-shm").write_bytes(b"shm")
            cache = Path(directory) / "cache"
            snapshot = create_wechat_snapshot(source, cache)

            self.assertEqual(
                {"MSG0.db", "MSG0.db-wal", "MSG0.db-shm"},
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
                create_wechat_snapshot(source, cache, retries=1, copy_file=mutate_after_copy)

    @staticmethod
    def _create_v3_fixture(root: Path) -> None:
        root.mkdir(parents=True)
        connection = sqlite3.connect(root / "MicroMsg.db")
        with connection:
            connection.executescript(
                """
                CREATE TABLE Contact (UserName TEXT, Type INTEGER, Remark TEXT, NickName TEXT);
                INSERT INTO Contact VALUES ('wxid_self', 1, '', '我');
                INSERT INTO Contact VALUES ('wxid_friend', 1, '小雨', '雨');
                """
            )
        connection.close()
        connection = sqlite3.connect(root / "MSG0.db")
        with connection:
            connection.executescript(
                """
                CREATE TABLE MSG (
                    localId INTEGER, MsgSvrID INTEGER, Type INTEGER, SubType INTEGER,
                    IsSender INTEGER, CreateTime INTEGER, StrTalker TEXT, StrContent TEXT
                );
                INSERT INTO MSG VALUES (1, 101, 1, 0, 0, 1780000000, 'wxid_friend', '你好');
                INSERT INTO MSG VALUES (2, 102, 3, 0, 1, 1780000060, 'wxid_friend', 'image.xml');
                """
            )
        connection.close()

    @staticmethod
    def _create_v4_fixture(root: Path, chat_id: str) -> None:
        (root / "contact").mkdir(parents=True)
        (root / "session").mkdir()
        (root / "message").mkdir()
        connection = sqlite3.connect(root / "contact" / "contact.db")
        with connection:
            connection.executescript(
                """
                CREATE TABLE contact (username TEXT, local_type INTEGER, remark TEXT, nick_name TEXT);
                INSERT INTO contact VALUES ('wxid_self', 1, '', '我');
                INSERT INTO contact VALUES ('wxid_friend', 1, '小雨', '雨');
                """
            )
        connection.close()
        connection = sqlite3.connect(root / "session" / "session.db")
        with connection:
            connection.executescript(
                """
                CREATE TABLE SessionTable (username TEXT, last_timestamp INTEGER);
                INSERT INTO SessionTable VALUES ('wxid_friend', 1780000000);
                """
            )
        connection.close()
        table_name = f"Msg_{hashlib.md5(chat_id.encode('utf-8')).hexdigest()}"
        connection = sqlite3.connect(root / "message" / "message_0.db")
        with connection:
            connection.execute("CREATE TABLE Name2Id (user_name TEXT)")
            connection.execute("INSERT INTO Name2Id(rowid, user_name) VALUES (1, ?)", (chat_id,))
            connection.execute(
                f'''CREATE TABLE "{table_name}" (
                    local_id INTEGER, server_id INTEGER, local_type INTEGER, sort_seq INTEGER,
                    real_sender_id INTEGER, create_time INTEGER, message_content TEXT
                )'''
            )
            connection.execute(
                f'''INSERT INTO "{table_name}" VALUES (1, 201, 1, 1, 1, 1780000000, 'v4 明文消息')'''
            )
        connection.close()


if __name__ == "__main__":
    unittest.main()
