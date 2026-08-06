import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from src.preprocessing.parser_registry import ParserError, ParserRegistry
from src.preprocessing.data_parser import ChatDataParser


class ParserRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ParserRegistry.with_builtins()

    def test_selects_json_by_content_signature_not_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wechat-export.txt"
            path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "sender": "wxid_1",
                                "sender_name": "小雨",
                                "message": "晚安",
                                "timestamp": "2026-08-05T22:00:00+08:00",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_json", result.source_type)
        self.assertEqual(1, result.summary["record_count"])
        self.assertEqual("wxid_1", result.records[0].sender_id)
        self.assertEqual("晚安", result.records[0].content)

    def test_streams_text_into_canonical_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.data"
            path.write_text(
                "[2026-08-05 21:00] 小雨: 你好\n[2026-08-05 21:01] 我: 在吗\n",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_text", result.source_type)
        self.assertEqual(2, result.summary["record_count"])
        self.assertEqual("小雨", result.records[0].sender_id)
        self.assertEqual("你好", result.records[0].content)
        self.assertEqual("text", result.records[1].message_type)

    def test_parses_common_txt_timestamp_variants_and_multiline_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.txt"
            path.write_text(
                "2026-08-05 21:00:00 小雨: 第一行\n"
                "继续内容\n"
                "[2026/08/05 21:01] 我：第二条\n",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_text", result.source_type)
        self.assertEqual(2, len(result.records))
        self.assertEqual("小雨", result.records[0].sender_id)
        self.assertEqual("第一行\n继续内容", result.records[0].content)
        self.assertEqual("2026-08-05 21:00:00", result.records[0].timestamp)
        self.assertEqual("我", result.records[1].sender_id)
        self.assertEqual("第二条", result.records[1].content)

    def test_decodes_utf16_txt_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.txt"
            path.write_text(
                "[2026-08-05 21:00] 小雨: UTF-16 消息\n",
                encoding="utf-16",
            )

            result = self.registry.parse(path)

        self.assertEqual(1, len(result.records))
        self.assertEqual("UTF-16 消息", result.records[0].content)

    def test_parses_sender_only_txt_lines_with_line_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.txt"
            path.write_text("小雨：没有时间也要保留发送者\n", encoding="utf-8")

            result = self.registry.parse(path)

        self.assertEqual("小雨", result.records[0].sender_id)
        self.assertEqual("没有时间也要保留发送者", result.records[0].content)
        self.assertEqual("line:1", result.records[0].timestamp)

    def test_assigns_stable_record_ids_during_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.data"
            path.write_text(
                "[2026-08-05 21:00] 小雨: 你好\n[2026-08-05 21:01] 我: 在吗\n",
                encoding="utf-8",
            )

            first = self.registry.parse(
                path,
                {"record_id_namespace": "import-123", "source_name": "chat.data"},
            )
            second = self.registry.parse(
                path,
                {"record_id_namespace": "import-123", "source_name": "chat.data"},
            )
            other_import = self.registry.parse(
                path,
                {"record_id_namespace": "import-456", "source_name": "chat.data"},
            )

        first_ids = [record.record_id for record in first.records]
        second_ids = [record.record_id for record in second.records]
        other_ids = [record.record_id for record in other_import.records]
        self.assertEqual(first_ids, second_ids)
        self.assertNotEqual(first_ids, other_ids)
        self.assertEqual(
            hashlib.sha256(b"import-123:generic_text:0").hexdigest(),
            first_ids[0],
        )
        self.assertEqual(first_ids[0], first.records[0].to_dict()["record_id"])

    def test_supports_jsonl_as_a_streaming_format(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "sender_id": "qq_1",
                                "content": "收到",
                                "timestamp": "2026-08-05T21:00:00+08:00",
                            }
                        ),
                        json.dumps(
                            {
                                "sender_id": "qq_2",
                                "content": "好的",
                                "timestamp": "2026-08-05T21:01:00+08:00",
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_jsonl", result.source_type)
        self.assertEqual(["收到", "好的"], [record.content for record in result.records])

    def test_unsupported_content_returns_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.bin"
            path.write_bytes(b"\x00\x01not-a-supported-chat-format")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("unsupported_format", raised.exception.code)
        self.assertIn("parser", str(raised.exception).lower())

    def test_invalid_record_is_not_reported_as_empty_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.json"
            path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {"sender_id": "missing-timestamp", "content": "不完整"}
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("invalid_record", raised.exception.code)

    def test_legacy_parser_facade_uses_content_probing_and_keeps_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "export.any"
            path.write_text(
                json.dumps(
                    [
                        {
                            "sender_id": "wxid_2",
                            "content": "兼容门面",
                            "timestamp": "2026-08-05T21:02:00+08:00",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            records = ChatDataParser().parse_chat_data(str(path))

        self.assertEqual("wxid_2", records[0]["sender_id"])
        self.assertEqual("wxid_2", records[0]["sender"])
        self.assertEqual("兼容门面", records[0]["content"])
        self.assertEqual("兼容门面", records[0]["message"])

    def test_limits_preview_records_and_marks_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.txt"
            path.write_text(
                "\n".join(
                    f"[2026-08-05 21:0{index}] 小雨: 消息 {index}" for index in range(3)
                ),
                encoding="utf-8",
            )

            result = self.registry.parse(path, max_records=2)

        self.assertEqual(2, len(result.records))
        self.assertEqual(2, result.summary["record_count"])
        self.assertTrue(result.summary["truncated"])


if __name__ == "__main__":
    unittest.main()
