"""P1-08/P1-09 document conversation parser contracts."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.preprocessing.parser_registry import ParserError, ParserRegistry


class DocumentParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ParserRegistry.with_builtins()

    def test_parses_docx_conversation_paragraphs(self) -> None:
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'
            '<w:p><w:r><w:t>2026-08-10 10:00:00 - u1: hello</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>2026-08-10 10:01:00 - u2: received</w:t></w:r></w:p>'
            '</w:body></w:document>'
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.docx"
            self._write_docx(path, document_xml)

            result = self.registry.parse(path)

        self.assertEqual("generic_docx", result.source_type)
        self.assertEqual(["hello", "received"], [record.content for record in result.records])
        self.assertEqual("u1", result.records[0].sender_id)

    def test_rejects_docx_zip_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("../outside.txt", "unsafe")
                archive.writestr("word/document.xml", "<document />")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("unsafe_archive", raised.exception.code)

    def test_parses_text_pdf_without_optional_library(self) -> None:
        pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Length 62 >>\nstream\n"
            b"BT /F1 12 Tf 72 720 Td (2026-08-10 10:00:00 - u1: hello) Tj ET\n"
            b"endstream\nendobj\n%%EOF\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.pdf"
            path.write_bytes(pdf)

            result = self.registry.parse(path)

        self.assertEqual("generic_pdf", result.source_type)
        self.assertEqual("hello", result.records[0].content)

    def test_pdf_text_array_is_not_collected_twice(self) -> None:
        pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Length 70 >>\nstream\n"
            b"BT /F1 12 Tf [(2026-08-10 10:00:00 - u1: hello)] TJ ET\n"
            b"endstream\nendobj\n%%EOF\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "array.pdf"
            path.write_bytes(pdf)

            result = self.registry.parse(path)

        self.assertEqual(1, len(result.records))
        self.assertEqual("hello", result.records[0].content)

    def test_decodes_utf16be_hex_pdf_text_in_fallback(self) -> None:
        text = "2026-08-10 10:00:00 - 小明: 你好"
        encoded = (b"FEFF" + text.encode("utf-16-be").hex().upper().encode("ascii"))
        pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Length 80 >>\nstream\n"
            b"BT /F1 12 Tf 72 720 Td <" + encoded + b"> Tj ET\n"
            b"endstream\nendobj\n%%EOF\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "utf16.pdf"
            path.write_bytes(pdf)

            result = self.registry.parse(path)

        self.assertEqual("小明", result.records[0].sender_id)
        self.assertEqual("你好", result.records[0].content)

    def test_rejects_non_pdf_content_with_stable_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chat.pdf"
            path.write_bytes(b"not a PDF")

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("unsupported_format", raised.exception.code)

    def test_rejects_encrypted_pdf_without_guessing_keys(self) -> None:
        pdf = b"%PDF-1.4\n1 0 obj\n<< /Encrypt 2 0 R >>\n%%EOF\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "encrypted.pdf"
            path.write_bytes(pdf)

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("encrypted_document", raised.exception.code)

    def test_rejects_pdf_over_page_limit(self) -> None:
        pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Count 513 /Length 62 >>\nstream\n"
            b"BT /F1 12 Tf 72 720 Td (2026-08-10 10:00:00 - u1: hello) Tj ET\n"
            b"endstream\nendobj\n%%EOF\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "too-many-pages.pdf"
            path.write_bytes(pdf)

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("document_too_large", raised.exception.code)

    def test_rejects_docx_without_message_records(self) -> None:
        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Contact list only</w:t></w:r></w:p></w:body></w:document>'
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.docx"
            self._write_docx(path, document_xml)

            with self.assertRaises(ParserError) as raised:
                self.registry.parse(path)

        self.assertEqual("unsupported_format", raised.exception.code)

    def test_preserves_docx_line_breaks_and_tabs_inside_message(self) -> None:
        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>2026-08-10 10:00:00 - u1: first</w:t>'
            '<w:br/><w:t>second</w:t><w:tab/><w:t>column</w:t></w:r></w:p>'
            '</w:body></w:document>'
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "line-breaks.docx"
            self._write_docx(path, document_xml)

            result = self.registry.parse(path)

        self.assertEqual("first\nsecond\tcolumn", result.records[0].content)

    @staticmethod
    def _write_docx(path: Path, document_xml: bytes) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types />")
            archive.writestr("word/document.xml", document_xml)


if __name__ == "__main__":
    unittest.main()
