import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.preprocessing.parser_registry import ParserError, ParserRegistry


class WeChatBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ParserRegistry.with_builtins()

    def test_parses_manifest_jsonl_backup_without_mutating_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wechat-backup.zip"
            self._write_valid_archive(path)
            source_bytes = path.read_bytes()

            result = self.registry.parse(path)

            self.assertEqual(source_bytes, path.read_bytes())

        self.assertEqual("wechat_backup", result.source_type)
        self.assertEqual(["你好", "晚安"], [item.content for item in result.records])
        self.assertEqual("wxid_1", result.records[0].sender_id)
        self.assertEqual("小雨", result.records[0].sender_name)
        self.assertTrue(all(item.record_id for item in result.records))
        self.assertEqual("manifest_v1", result.summary["schema"])

    def test_limits_preview_records_and_reports_bounded_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wechat-backup.zip"
            self._write_valid_archive(path)

            result = self.registry.parse(path, max_records=1)

        self.assertEqual(1, len(result.records))
        self.assertTrue(result.summary["truncated"])
        self.assertEqual("bounded_archive", result.summary["snapshot"])

    def test_rejects_path_traversal_entries_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wechat-backup.zip"
            manifest = {
                "schema_version": 1,
                "platform": "wechat",
                "files": [{"path": "../outside.jsonl", "format": "jsonl"}],
            }
            self._write_zip(path, {"manifest.json": manifest, "../outside.jsonl": "{}\n"})

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path, {"source_type": "wechat_backup"})

        self.assertEqual("path_traversal", raised.exception.code)
        self.assertNotIn("outside", str(raised.exception))

    def test_rejects_nested_archive_and_unbounded_entry_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nested_path = Path(directory) / "nested.zip"
            nested_manifest = {
                "schema_version": 1,
                "platform": "wechat",
                "files": [{"path": "nested.zip", "format": "zip"}],
            }
            self._write_zip(
                nested_path,
                {"manifest.json": nested_manifest, "nested.zip": b"PK\x03\x04"},
            )

            with self.assertRaises(ParserError) as nested_raised:
                self.registry.parse(nested_path, {"source_type": "wechat_backup"})

            many_path = Path(directory) / "many.zip"
            entries = {"manifest.json": {"schema_version": 1, "platform": "wechat", "records": []}}
            entries.update({f"extra-{index}.txt": "x" for index in range(1025)})
            self._write_zip(many_path, entries)

            with self.assertRaises(ParserError) as count_raised:
                self.registry.parse(many_path, {"source_type": "wechat_backup"})

        self.assertEqual("nested_archive", nested_raised.exception.code)
        self.assertEqual("archive_limits_exceeded", count_raised.exception.code)

    def test_rejects_missing_or_non_wechat_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.zip"
            self._write_zip(missing, {"messages.jsonl": "{}\n"})
            with self.assertRaises(ParserError) as missing_raised:
                self.registry.parse(missing, {"source_type": "wechat_backup"})

            wrong_platform = Path(directory) / "wrong-platform.zip"
            self._write_zip(
                wrong_platform,
                {
                    "manifest.json": {
                        "schema_version": 1,
                        "platform": "qq",
                        "records": [{"sender": "qq_1", "message": "不是微信"}],
                    }
                },
            )
            with self.assertRaises(ParserError) as platform_raised:
                self.registry.parse(wrong_platform, {"source_type": "wechat_backup"})

        self.assertEqual("unsupported_manifest", missing_raised.exception.code)
        self.assertEqual("unsupported_manifest", platform_raised.exception.code)

    def test_rejects_corrupt_zip_with_stable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wechat-backup.zip"
            path.write_bytes(b"not a zip archive")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path, {"source_type": "wechat_backup"})

        self.assertEqual("corrupt_archive", raised.exception.code)
        self.assertNotIn("BadZipFile", str(raised.exception))

    @staticmethod
    def _write_valid_archive(path: Path) -> None:
        manifest = {
            "schema_version": 1,
            "platform": "wechat",
            "files": [{"path": "messages.jsonl", "format": "jsonl"}],
        }
        messages = (
            json.dumps(
                {
                    "sender_id": "wxid_1",
                    "sender_name": "小雨",
                    "content": "你好",
                    "timestamp": "2026-08-07T10:00:00+08:00",
                },
                ensure_ascii=False,
            )
            + "\n"
            + json.dumps(
                {
                    "sender_id": "wxid_self",
                    "sender_name": "我",
                    "content": "晚安",
                    "timestamp": "2026-08-07T22:00:00+08:00",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        WeChatBackupTests._write_zip(path, {"manifest.json": manifest, "messages.jsonl": messages})

    @staticmethod
    def _write_zip(path: Path, entries: dict[str, object]) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, value in entries.items():
                if isinstance(value, dict):
                    value = json.dumps(value, ensure_ascii=False)
                if isinstance(value, str):
                    value = value.encode("utf-8")
                archive.writestr(name, io.BytesIO(value).getvalue())


if __name__ == "__main__":
    unittest.main()
