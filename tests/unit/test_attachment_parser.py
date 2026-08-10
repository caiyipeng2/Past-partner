"""P1-05 attachment reference normalization contracts."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.domain.messages import MessageValidationError, NormalizedMessage
from src.preprocessing.parser_registry import ParserError, ParserRegistry


class AttachmentParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ParserRegistry.with_builtins()

    def test_normalizes_attachment_metadata_and_infers_media_type(self) -> None:
        message = NormalizedMessage.from_mapping(
            {
                "sender_id": "u1",
                "content": "照片",
                "timestamp": "2026-08-10T10:00:00+08:00",
                "attachments": [
                    {"path": "photos/IMG_01.JPG", "size": "42"},
                ],
            }
        )

        self.assertEqual(
            {
                "path": "photos/IMG_01.JPG",
                "size": 42,
                "kind": "image",
                "media_type": "image/jpeg",
                "name": "IMG_01.JPG",
            },
            message.attachments[0],
        )

    def test_rejects_attachment_path_traversal(self) -> None:
        with self.assertRaises(MessageValidationError):
            NormalizedMessage.from_mapping(
                {
                    "sender_id": "u1",
                    "content": "不安全附件",
                    "timestamp": "2026-08-10T10:00:00+08:00",
                    "attachments": [{"path": "../secret.jpg"}],
                }
            )

    def test_rejects_inline_media_bytes(self) -> None:
        with self.assertRaises(MessageValidationError):
            NormalizedMessage.from_mapping(
                {
                    "sender_id": "u1",
                    "content": "内嵌数据",
                    "timestamp": "2026-08-10T10:00:00+08:00",
                    "attachments": [{"data": "AAECAw==", "name": "raw.bin"}],
                }
            )

    def test_registry_reports_unsafe_attachment_as_invalid_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.json"
            path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "sender_id": "u1",
                                "content": "危险路径",
                                "timestamp": "2026-08-10T10:00:00+08:00",
                                "attachments": [{"path": "C:/private/photo.jpg"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("invalid_record", raised.exception.code)

    def test_parses_json_attachment_list_without_raw_media_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.json"
            path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "sender_id": "u1",
                                "content": "语音",
                                "timestamp": "2026-08-10T10:00:00+08:00",
                                "attachments": [
                                    {"name": "note.m4a", "media_type": "audio/mp4"},
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        attachment = result.records[0].attachments[0]
        self.assertEqual("audio", attachment["kind"])
        self.assertEqual("note.m4a", attachment["name"])
        self.assertNotIn("content", attachment)
        self.assertNotIn("bytes", attachment)

    def test_parses_csv_attachment_reference_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.csv"
            path.write_text(
                "sender,content,timestamp,attachment\n"
                "u1,看图,2026-08-10 10:00:00,photo.jpg|image/jpeg|128\n",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("generic_csv", result.source_type)
        self.assertEqual(
            {
                "path": "photo.jpg",
                "name": "photo.jpg",
                "media_type": "image/jpeg",
                "kind": "image",
                "size": 128,
            },
            result.records[0].attachments[0],
        )

    def test_parses_xml_attachment_element(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.xml"
            path.write_text(
                "<conversation><message sender=\"u1\" timestamp=\"2026-08-10 10:00:00\">"
                "<content>文件</content><attachments>"
                "<file path=\"docs/readme.pdf\" size=\"256\" /></attachments>"
                "</message></conversation>",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("file", result.records[0].attachments[0]["kind"])
        self.assertEqual("application/pdf", result.records[0].attachments[0]["media_type"])
        self.assertEqual(256, result.records[0].attachments[0]["size"])

    def test_parses_html_media_reference_inside_message(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.html"
            path.write_text(
                "<html><body><div class=\"chat-item\" data-sender=\"u1\" "
                "data-time=\"2026-08-10 10:00:00\"><span class=\"content\">图片</span>"
                "<img src=\"images/photo.webp\" width=\"20\" /></div></body></html>",
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("image", result.records[0].attachments[0]["kind"])
        self.assertEqual("image/webp", result.records[0].attachments[0]["media_type"])
        self.assertEqual("images/photo.webp", result.records[0].attachments[0]["path"])

    def test_attachment_only_json_message_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.json"
            path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "sender_id": "u1",
                                "content": "",
                                "timestamp": "2026-08-10T10:00:00+08:00",
                                "attachments": [{"name": "voice.ogg"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = self.registry.parse(path)

        self.assertEqual("audio", result.records[0].attachments[0]["kind"])


if __name__ == "__main__":
    unittest.main()
