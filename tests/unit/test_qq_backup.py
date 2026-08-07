import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.preprocessing.parser_registry import ParserError, ParserRegistry


class QqBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ParserRegistry.with_builtins()

    def test_parses_manifest_qq_text_backup_without_mutating_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq-backup.zip"
            self._write_valid_archive(path)
            source_bytes = path.read_bytes()

            result = self.registry.parse(path)

            self.assertEqual(source_bytes, path.read_bytes())

        self.assertEqual("qq_backup", result.source_type)
        self.assertEqual(["第一条 QQ 消息", "回复消息"], [item.content for item in result.records])
        self.assertEqual("小雨(qq_1)", result.records[0].sender_id)
        self.assertEqual("2026-08-05 21:00:00", result.records[0].timestamp)
        self.assertTrue(all(item.record_id for item in result.records))
        self.assertEqual("manifest_v1", result.summary["schema"])

    def test_limits_preview_records_and_reports_bounded_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq-backup.zip"
            self._write_valid_archive(path)

            result = self.registry.parse(path, max_records=1)

        self.assertEqual(1, len(result.records))
        self.assertTrue(result.summary["truncated"])
        self.assertEqual("bounded_archive", result.summary["snapshot"])

    def test_rejects_path_traversal_entries_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq-backup.zip"
            manifest = {
                "schema_version": 1,
                "platform": "qq",
                "files": [{"path": "../outside.jsonl", "format": "jsonl"}],
            }
            self._write_zip(path, {"manifest.json": manifest, "../outside.jsonl": "{}\n"})

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path, {"source_type": "qq_backup"})

        self.assertEqual("path_traversal", raised.exception.code)
        self.assertNotIn("outside", str(raised.exception))

    def test_rejects_nested_archive_and_unbounded_entry_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nested_path = Path(directory) / "nested.zip"
            nested_manifest = {
                "schema_version": 1,
                "platform": "qq",
                "files": [{"path": "nested.zip", "format": "zip"}],
            }
            self._write_zip(
                nested_path,
                {"manifest.json": nested_manifest, "nested.zip": b"PK\x03\x04"},
            )

            with self.assertRaises(ParserError) as nested_raised:
                self.registry.parse(nested_path, {"source_type": "qq_backup"})

            many_path = Path(directory) / "many.zip"
            entries = {"manifest.json": {"schema_version": 1, "platform": "qq", "records": []}}
            entries.update({f"extra-{index}.txt": "x" for index in range(1025)})
            self._write_zip(many_path, entries)

            with self.assertRaises(ParserError) as count_raised:
                self.registry.parse(many_path, {"source_type": "qq_backup"})

        self.assertEqual("nested_archive", nested_raised.exception.code)
        self.assertEqual("archive_limits_exceeded", count_raised.exception.code)

    def test_rejects_missing_or_non_qq_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.zip"
            self._write_zip(missing, {"messages.jsonl": "{}\n"})
            with self.assertRaises(ParserError) as missing_raised:
                self.registry.parse(missing, {"source_type": "qq_backup"})

            wrong_platform = Path(directory) / "wrong-platform.zip"
            self._write_zip(
                wrong_platform,
                {
                    "manifest.json": {
                        "schema_version": 1,
                        "platform": "wechat",
                        "records": [{"sender": "wxid_1", "message": "不是 QQ"}],
                    }
                },
            )
            with self.assertRaises(ParserError) as platform_raised:
                self.registry.parse(wrong_platform, {"source_type": "qq_backup"})

        self.assertEqual("unsupported_manifest", missing_raised.exception.code)
        self.assertEqual("unsupported_manifest", platform_raised.exception.code)

    def test_rejects_corrupt_zip_with_stable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq-backup.zip"
            path.write_bytes(b"not a zip archive")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path, {"source_type": "qq_backup"})

        self.assertEqual("corrupt_archive", raised.exception.code)
        self.assertNotIn("BadZipFile", str(raised.exception))

    @staticmethod
    def _write_valid_archive(path: Path) -> None:
        manifest = {
            "schema_version": 1,
            "platform": "qq",
            "files": [{"path": "messages.txt", "format": "qq_text"}],
        }
        messages = (
            "QQ聊天记录导出\n"
            "2026-08-05 21:00:00 小雨(qq_1)\n"
            "第一条 QQ 消息\n"
            "2026-08-05 21:01:00 我(qq_2): 回复消息\n"
        )
        QqBackupTests._write_zip(path, {"manifest.json": manifest, "messages.txt": messages})

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
