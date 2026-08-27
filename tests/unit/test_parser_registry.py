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

    def test_parses_wechat_text_export_with_timestamp_sender_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "微信聊天记录.txt"
            path.write_text(
                "微信聊天记录导出\n"
                "2026-08-05 21:00:00 小雨\n"
                "第一条消息\n"
                "第二行\n"
                "2026-08-05 21:01:00 我: 回复消息\n",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("wechat_text", result.source_type)
        self.assertEqual(2, len(result.records))
        self.assertEqual("小雨", result.records[0].sender_id)
        self.assertEqual("第一条消息\n第二行", result.records[0].content)
        self.assertEqual("2026-08-05 21:00:00", result.records[0].timestamp)
        self.assertEqual("我", result.records[1].sender_id)
        self.assertEqual("回复消息", result.records[1].content)

    def test_parses_wechat_html_export_with_nested_content_and_entities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wechat-export.html"
            path.write_text(
                "<!doctype html><html data-platform=\"wechat\"><body>"
                '<div class="message" data-sender-id="wxid_1" '
                'data-sender-name="小雨" data-timestamp="2026-08-05T21:00:00+08:00">'
                '<span class="content">你好 &amp; 晚安</span></div>'
                '<div class="message">'
                '<time datetime="2026-08-05T21:01:00+08:00">21:01</time>'
                '<span class="sender">我</span>'
                '<div class="message-content"><b>收到</b> 了</div>'
                "</div></body></html>",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("wechat_html", result.source_type)
        self.assertEqual(2, len(result.records))
        self.assertEqual("wxid_1", result.records[0].sender_id)
        self.assertEqual("小雨", result.records[0].sender_name)
        self.assertEqual("你好 & 晚安", result.records[0].content)
        self.assertEqual("2026-08-05T21:00:00+08:00", result.records[0].timestamp)
        self.assertEqual("我", result.records[1].sender_id)
        self.assertEqual("收到 了", result.records[1].content)

    def test_decodes_utf16_wechat_html_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "微信聊天.html"
            path.write_text(
                '<html data-platform="wechat"><div class="message" '
                'data-sender="wxid_1" data-time="2026-08-05 21:00">'
                '<span class="text">UTF-16 微信消息</span></div></html>',
                encoding="utf-16",
            )

            result = self.registry.parse(path)

        self.assertEqual("wechat_html", result.source_type)
        self.assertEqual("UTF-16 微信消息", result.records[0].content)

    def test_parses_qq_text_export_with_timestamp_sender_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "QQ聊天记录.txt"
            path.write_text(
                "QQ聊天记录导出\n"
                "2026-08-05 21:00:00 小雨(qq_1)\n"
                "第一条 QQ 消息\n"
                "2026-08-05 21:01:00 我(qq_2): 回复消息\n",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("qq_text", result.source_type)
        self.assertEqual(2, len(result.records))
        self.assertEqual("小雨(qq_1)", result.records[0].sender_id)
        self.assertEqual("第一条 QQ 消息", result.records[0].content)
        self.assertEqual("2026-08-05 21:00:00", result.records[0].timestamp)
        self.assertEqual("我(qq_2)", result.records[1].sender_id)
        self.assertEqual("回复消息", result.records[1].content)

    def test_parses_qq_html_export_with_nested_content_and_entities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "qq-export.html"
            path.write_text(
                "<!doctype html><html data-platform=\"qq\"><body>"
                '<div class="message" data-sender-id="qq_1" '
                'data-sender-name="小雨" data-timestamp="2026-08-05T21:00:00+08:00">'
                '<span class="content">你好 &amp; 晚安</span></div>'
                '<div class="message">'
                '<time datetime="2026-08-05T21:01:00+08:00">21:01</time>'
                '<span class="sender">我</span>'
                '<div class="message-content"><b>收到</b> 了</div>'
                "</div></body></html>",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("qq_html", result.source_type)
        self.assertEqual(2, len(result.records))
        self.assertEqual("qq_1", result.records[0].sender_id)
        self.assertEqual("小雨", result.records[0].sender_name)
        self.assertEqual("你好 & 晚安", result.records[0].content)
        self.assertEqual("2026-08-05T21:00:00+08:00", result.records[0].timestamp)
        self.assertEqual("我", result.records[1].sender_id)
        self.assertEqual("收到 了", result.records[1].content)

    def test_decodes_utf16_qq_html_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "QQ聊天.html"
            path.write_text(
                '<html data-platform="qq"><div class="message" '
                'data-sender="qq_1" data-time="2026-08-05 21:00">'
                '<span class="text">UTF-16 QQ 消息</span></div></html>',
                encoding="utf-16",
            )

            result = self.registry.parse(path)

        self.assertEqual("qq_html", result.source_type)
        self.assertEqual("UTF-16 QQ 消息", result.records[0].content)

    def test_parses_generic_html_export_with_semantic_fields_and_entities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.html"
            path.write_text(
                "<!doctype html><html><body>"
                '<article class="chat-entry" data-author-id="user-1" '
                'data-author-name="Alice" data-timestamp="2026-08-10T09:00:00Z">'
                '<span class="author">Alice</span>'
                '<p class="message-text">Hello &amp; <strong>goodbye</strong></p>'
                "</article>"
                '<article class="record" data-sender="user-2" data-time="2026-08-10 09:01">'
                '<time datetime="2026-08-10 09:01">09:01</time>'
                '<div data-field="content">Reply<br>with a second line</div>'
                "</article></body></html>",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_html", result.source_type)
        self.assertEqual(2, len(result.records))
        self.assertEqual("user-1", result.records[0].sender_id)
        self.assertEqual("Alice", result.records[0].sender_name)
        self.assertEqual("Hello & goodbye", result.records[0].content)
        self.assertEqual("2026-08-10T09:00:00Z", result.records[0].timestamp)
        self.assertEqual("user-2", result.records[1].sender_id)
        self.assertEqual("Reply\nwith a second line", result.records[1].content)

    def test_decodes_utf16_generic_html_without_html_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.data"
            path.write_text(
                '<section class="message-item" data-sender-id="u1" '
                'data-sender-name="UTF 用户" data-time="2026-08-10 10:00">'
                '<div class="content">UTF-16 通用消息</div></section>',
                encoding="utf-16",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_html", result.source_type)
        self.assertEqual("UTF-16 通用消息", result.records[0].content)

    def test_ignores_script_and_style_content_in_generic_html(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.htm"
            path.write_text(
                "<html><head><style>.message{display:none}</style>"
                "<script>var fake = 'secret script message';</script></head><body>"
                '<div class="message" data-sender="u1" data-time="2026-08-10 11:00">'
                '<span class="text">Visible message</span></div></body></html>',
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_html", result.source_type)
        self.assertEqual(1, len(result.records))
        self.assertEqual("Visible message", result.records[0].content)
        self.assertNotIn("secret", result.records[0].content)

    def test_rejects_generic_html_without_message_containers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "landing.html"
            path.write_text(
                "<html><body><h1>Not a conversation</h1>"
                "<p>This page has no chat records.</p></body></html>",
                encoding="utf-8",
            )

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("unsupported_format", raised.exception.code)

    def test_limits_generic_html_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "conversation.html"
            path.write_text(
                "<html><body>"
                '<div class="chat-item" data-sender="u1" data-time="2026-08-10 12:00">'
                '<span class="content">one</span></div>'
                '<div class="chat-item" data-sender="u2" data-time="2026-08-10 12:01">'
                '<span class="content">two</span></div>'
                "</body></html>",
                encoding="utf-8",
            )

            result = self.registry.parse(path, max_records=1)

        self.assertEqual("generic_html", result.source_type)
        self.assertEqual(1, len(result.records))
        self.assertTrue(result.summary["truncated"])

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

    def test_parses_csv_by_header_signature_and_normalizes_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat-export.any"
            path.write_text(
                "sender_name,message,time,type\n"
                "小雨,你好,2026-08-05T21:00:00+08:00,text\n"
                "我,收到,2026-08-05T21:01:00+08:00,text\n",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_csv", result.source_type)
        self.assertEqual(["小雨", "我"], [record.sender_id for record in result.records])
        self.assertEqual(["你好", "收到"], [record.content for record in result.records])
        self.assertEqual("2026-08-05T21:00:00+08:00", result.records[0].timestamp)

    def test_parses_utf16_semicolon_csv_and_honors_record_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.csv"
            path.write_text(
                "sender;content;timestamp\n"
                "qq_1;第一条;2026-08-05 21:00:00\n"
                "qq_2;第二条;2026-08-05 21:01:00\n",
                encoding="utf-16",
            )

            result = self.registry.parse(path, max_records=1)

        self.assertEqual("generic_csv", result.source_type)
        self.assertEqual(1, len(result.records))
        self.assertTrue(result.summary["truncated"])
        self.assertEqual("第一条", result.records[0].content)

    def test_rejects_csv_without_required_chat_headers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.csv"
            path.write_text("name,email\n小雨,xiaoyu@example.com\n", encoding="utf-8")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("unsupported_format", raised.exception.code)
        self.assertIn("CSV", str(raised.exception))

    def test_reports_malformed_csv_rows_as_invalid_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.csv"
            path.write_text(
                "sender,content,timestamp\n"
                "小雨,未闭合的消息,2026-08-05 21:00:00\n"
                '我,"缺少结束引号,2026-08-05 21:01:00\n',
                encoding="utf-8",
            )

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("invalid_record", raised.exception.code)
        self.assertIn("CSV", str(raised.exception))

    def test_parses_xml_message_elements_by_content_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat-export.data"
            path.write_text(
                "<conversation><messages>"
                '<message sender_id="wxid_1" sender_name="小雨" '
                'timestamp="2026-08-05T21:00:00+08:00"><content>你好</content></message>'
                "<message><sender>我</sender><time>2026-08-05T21:01:00+08:00</time>"
                "<text>收到</text></message>"
                "</messages></conversation>",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_xml", result.source_type)
        self.assertEqual(["wxid_1", "我"], [record.sender_id for record in result.records])
        self.assertEqual(["你好", "收到"], [record.content for record in result.records])
        self.assertEqual("小雨", result.records[0].sender_name)

    def test_parses_utf16_xml_with_record_alias_and_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.xml"
            path.write_text(
                "<export><record><author>qq_1</author>"
                "<datetime>2026-08-05 21:00:00</datetime><body>第一条</body></record>"
                '<item sender="qq_2" time="2026-08-05 21:01:00">第二条</item></export>',
                encoding="utf-16",
            )

            result = self.registry.parse(path, max_records=1)

        self.assertEqual("generic_xml", result.source_type)
        self.assertEqual(1, len(result.records))
        self.assertTrue(result.summary["truncated"])
        self.assertEqual("第一条", result.records[0].content)

    def test_rejects_xml_without_message_elements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.xml"
            path.write_text("<contacts><contact><name>小雨</name></contact></contacts>", encoding="utf-8")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("unsupported_format", raised.exception.code)
        self.assertIn("XML", str(raised.exception))

    def test_rejects_xml_without_message_elements_even_without_xml_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.data"
            path.write_text("<contacts><contact><name>小雨</name></contact></contacts>", encoding="utf-8")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("unsupported_format", raised.exception.code)
        self.assertIn("XML", str(raised.exception))

    def test_rejects_xml_with_doctype_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.xml"
            path.write_text(
                "<!DOCTYPE conversation [<!ENTITY secret \"blocked\">]>"
                "<conversation><message sender=\"u1\" timestamp=\"2026-08-05\">"
                "<content>&secret;</content></message></conversation>",
                encoding="utf-8",
            )

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("unsupported_format", raised.exception.code)
        self.assertIn("DOCTYPE", str(raised.exception))

    def test_rejects_doctype_after_the_probe_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.xml"
            prefix = " " * (64 * 1024)
            path.write_text(
                prefix
                + "<!DOCTYPE conversation [<!ENTITY secret \"blocked\">]>"
                + "<conversation><message sender=\"u1\" timestamp=\"2026-08-05\">"
                + "<content>&secret;</content></message></conversation>",
                encoding="utf-8",
            )

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("unsupported_format", raised.exception.code)
        self.assertIn("DOCTYPE", str(raised.exception))

    def test_reports_malformed_xml_as_invalid_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.xml"
            path.write_text(
                "<conversation><message sender=\"u1\" timestamp=\"2026-08-05\">"
                "<content>未闭合</content></conversation>",
                encoding="utf-8",
            )

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("invalid_record", raised.exception.code)
        self.assertIn("XML", str(raised.exception))

    def test_decodes_utf16_json_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.json"
            path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "sender_id": "wxid_1",
                                "content": "UTF-16 JSON",
                                "timestamp": "2026-08-05T21:00:00+08:00",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-16",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_json", result.source_type)
        self.assertEqual("UTF-16 JSON", result.records[0].content)

    def test_decodes_utf16_jsonl_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "sender_id": "qq_1",
                        "content": "UTF-16 JSONL",
                        "timestamp": "2026-08-05T21:00:00+08:00",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-16",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_jsonl", result.source_type)
        self.assertEqual("UTF-16 JSONL", result.records[0].content)

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
