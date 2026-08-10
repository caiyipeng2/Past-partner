"""Safe DOCX and PDF conversation document parsers."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
from pathlib import Path, PurePosixPath
import zipfile
import zlib
from typing import Any, Iterator, Mapping, Sequence, TYPE_CHECKING
import xml.etree.ElementTree as ET

from src.domain.messages import MessageValidationError, NormalizedMessage

if TYPE_CHECKING:
    from src.preprocessing.parser_registry import ParserProbe, ParserSource, ParserValidation


MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
MAX_DOCUMENT_ENTRIES = 512
MAX_PDF_PAGES = 512
_TIMESTAMP = r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?"
_TEXT_LINE = re.compile(
    rf"^(?:\[(?P<bracket_timestamp>[^\]]+)\]|(?P<bare_timestamp>{_TIMESTAMP}))"
    r"\s*(?:[-|]\s*)?(?P<sender>[^:：]{1,128})\s*[:：]\s*(?P<content>.*)$"
)
_SENDER_LINE = re.compile(r"^(?P<sender>[^:：]{1,128})\s*[:：]\s*(?P<content>.+)$")
_PDF_LITERAL = re.compile(rb"\((?:\\.|[^\\)])*\)")
_PDF_HEX = re.compile(rb"<([0-9A-Fa-f]+)>")


class DocumentParserError(ValueError):
    """Actionable document failure with a stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _DocumentContent:
    lines: tuple[str, ...]
    page_count: int | None = None


class GenericDocxParser:
    source_type = "generic_docx"

    def probe(self, source: "ParserSource") -> "ParserProbe":
        from src.preprocessing.parser_registry import ParserProbe

        if not source.path.is_file():
            return ParserProbe(self.source_type, 0.0, False, "DOCX source must be a file")
        if source.metadata.get("source_type") == self.source_type:
            return ParserProbe(self.source_type, 0.99, True, "DOCX parser selected explicitly")
        if source.path.suffix.casefold() != ".docx":
            return ParserProbe(self.source_type, 0.0, False, "DOCX extension not recognized")
        if not zipfile.is_zipfile(source.path):
            return ParserProbe(self.source_type, 0.9, True, "DOCX extension recognized; archive validation pending")
        return ParserProbe(self.source_type, 0.97, True, "DOCX package recognized")

    def validate(self, source: "ParserSource") -> "ParserValidation":
        from src.preprocessing.parser_registry import ParserValidation

        try:
            content = _read_docx_content(source.path)
            if not _has_message_hint(content.lines):
                return ParserValidation(False, "unsupported_format", "DOCX contains no conversation-like message lines")
        except DocumentParserError as exc:
            return ParserValidation(False, exc.code, str(exc))
        return ParserValidation(True)

    def stream_records(self, source: "ParserSource") -> Iterator[NormalizedMessage]:
        content = _read_docx_content(source.path)
        yield from _records_from_lines(content.lines, source_label="docx")

    def summarize(
        self,
        records: Sequence[NormalizedMessage],
        warnings: Sequence[str],
        confidence: float,
    ) -> Mapping[str, Any]:
        return {
            "record_count": len(records),
            "warning_count": len(warnings),
            "unsupported_count": 0,
            "confidence": confidence,
            "document_type": "docx",
        }


class GenericPdfParser:
    source_type = "generic_pdf"

    def probe(self, source: "ParserSource") -> "ParserProbe":
        from src.preprocessing.parser_registry import ParserProbe

        if not source.path.is_file():
            return ParserProbe(self.source_type, 0.0, False, "PDF source must be a file")
        if source.metadata.get("source_type") == self.source_type:
            return ParserProbe(self.source_type, 0.99, True, "PDF parser selected explicitly")
        try:
            with source.path.open("rb") as handle:
                header = handle.read(5)
        except OSError:
            return ParserProbe(self.source_type, 0.0, False, "PDF source cannot be read")
        if header == b"%PDF-":
            return ParserProbe(self.source_type, 0.98, True, "PDF header recognized")
        if source.path.suffix.casefold() == ".pdf":
            return ParserProbe(self.source_type, 0.86, True, "PDF extension recognized; header validation pending")
        return ParserProbe(self.source_type, 0.0, False, "PDF header not recognized")

    def validate(self, source: "ParserSource") -> "ParserValidation":
        from src.preprocessing.parser_registry import ParserValidation

        try:
            content = _read_pdf_content(source.path)
            if not _has_message_hint(content.lines):
                return ParserValidation(False, "unsupported_format", "PDF contains no conversation-like message lines")
        except DocumentParserError as exc:
            return ParserValidation(False, exc.code, str(exc))
        return ParserValidation(True)

    def stream_records(self, source: "ParserSource") -> Iterator[NormalizedMessage]:
        content = _read_pdf_content(source.path)
        yield from _records_from_lines(content.lines, source_label="pdf")

    def summarize(
        self,
        records: Sequence[NormalizedMessage],
        warnings: Sequence[str],
        confidence: float,
    ) -> Mapping[str, Any]:
        return {
            "record_count": len(records),
            "warning_count": len(warnings),
            "unsupported_count": 0,
            "confidence": confidence,
            "document_type": "pdf",
        }


def _read_docx_content(path: Path) -> _DocumentContent:
    try:
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise DocumentParserError("document_too_large", "DOCX 文件超过解析大小上限")
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            _validate_docx_entries(infos)
            if "word/document.xml" not in archive.namelist():
                raise DocumentParserError("unsupported_format", "DOCX 缺少 word/document.xml 主文档")
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCUMENT_BYTES:
                raise DocumentParserError("document_too_large", "DOCX 主文档超过解析大小上限")
            document_xml = archive.read("word/document.xml")
    except DocumentParserError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise DocumentParserError("corrupt_document", "DOCX 文件无法安全读取") from exc

    if b"<!doctype" in document_xml.lower() or b"<!entity" in document_xml.lower():
        raise DocumentParserError("unsupported_format", "DOCX 不支持 DOCTYPE/ENTITY 声明")
    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise DocumentParserError("corrupt_document", "DOCX 主文档 XML 无法解析") from exc
    lines: list[str] = []
    for paragraph in root.iter():
        if _local_name(paragraph.tag) != "p":
            continue
        text = "".join(
            node.text or ""
            for node in paragraph.iter()
            if _local_name(node.tag) == "t"
        ).strip()
        if text:
            lines.append(text)
    return _DocumentContent(tuple(lines))


def _validate_docx_entries(infos: Sequence[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_DOCUMENT_ENTRIES:
        raise DocumentParserError("unsafe_archive", "DOCX 条目数量超过安全上限")
    total = 0
    seen: set[str] = set()
    for info in infos:
        normalized = info.filename.replace("\\", "/")
        parts = PurePosixPath(normalized).parts
        if (
            normalized.startswith("/")
            or re.match(r"^[a-zA-Z]:/", normalized)
            or any(part == ".." for part in parts)
        ):
            raise DocumentParserError("unsafe_archive", "DOCX 包含路径穿越条目")
        if normalized.casefold() in seen:
            raise DocumentParserError("unsafe_archive", "DOCX 包含重复条目")
        seen.add(normalized.casefold())
        if info.filename.casefold().endswith(".zip"):
            raise DocumentParserError("unsafe_archive", "DOCX 不支持嵌套压缩包")
        total += info.file_size
        if total > MAX_DOCUMENT_BYTES:
            raise DocumentParserError("document_too_large", "DOCX 展开大小超过安全上限")
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode and (mode & 0o170000) == 0o120000:
            raise DocumentParserError("unsafe_archive", "DOCX 不支持符号链接条目")


def _read_pdf_content(path: Path) -> _DocumentContent:
    try:
        size = path.stat().st_size
        if size > MAX_DOCUMENT_BYTES:
            raise DocumentParserError("document_too_large", "PDF 文件超过解析大小上限")
        data = path.read_bytes()
    except DocumentParserError:
        raise
    except OSError as exc:
        raise DocumentParserError("unsupported_format", "PDF 文件无法读取") from exc
    if not data.startswith(b"%PDF-"):
        raise DocumentParserError("unsupported_format", "文件不是有效 PDF")
    if b"/Encrypt" in data[: min(len(data), 2 * 1024 * 1024)]:
        raise DocumentParserError("encrypted_document", "加密 PDF 当前不会自动提取密钥")

    text = _extract_with_pypdf(data)
    if text is None:
        text = _extract_pdf_text_fallback(data)
    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    if not lines:
        raise DocumentParserError("unsupported_format", "PDF 中没有可提取的文本")
    page_count = _pdf_page_count(data)
    if page_count is not None and page_count > MAX_PDF_PAGES:
        raise DocumentParserError("document_too_large", "PDF 页数超过解析上限")
    return _DocumentContent(lines, page_count)


def _extract_with_pypdf(data: bytes) -> str | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise DocumentParserError("encrypted_document", "加密 PDF 当前不会自动提取密钥")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentParserError("document_too_large", "PDF 页数超过解析上限")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text if text.strip() else None
    except DocumentParserError:
        raise
    except Exception:
        # Keep a bounded operator fallback for minimal or partially recoverable
        # PDFs when pypdf cannot build a page tree.
        return None


def _extract_pdf_text_fallback(data: bytes) -> str:
    chunks: list[bytes] = []
    stream_pattern = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)
    for match in stream_pattern.finditer(data):
        chunk = match.group(1)
        context = data[max(0, match.start() - 256) : match.start()]
        if b"/FlateDecode" in context:
            try:
                chunk = zlib.decompress(chunk)
            except zlib.error:
                continue
        chunks.append(chunk)
    if not chunks:
        chunks = [data]
    text_parts: list[str] = []
    for chunk in chunks:
        for block in re.findall(rb"BT(.*?)ET", chunk, re.DOTALL):
            text_parts.extend(_pdf_text_operators(block))
    return "\n".join(part for part in text_parts if part)


def _pdf_text_operators(block: bytes) -> list[str]:
    parts: list[str] = []
    # Match strings only when they are attached to a text-showing operator.
    # Scanning every literal would also collect font/resource strings and would
    # collect each member of a TJ array twice.
    for match in re.finditer(rb"(\((?:\\.|[^\\)])*\)|<([0-9A-Fa-f]+)>)\s*Tj", block):
        literal = match.group(1)
        if literal.startswith(b"("):
            value = _decode_pdf_literal(literal[1:-1])
        else:
            try:
                value = bytes.fromhex(match.group(2).decode("ascii")).decode("utf-8", errors="replace")
            except ValueError:
                value = ""
        if value:
            parts.append(value.strip())
    for match in re.finditer(rb"\[(.*?)\]\s*TJ", block, re.DOTALL):
        for literal in _PDF_LITERAL.findall(match.group(1)):
            value = _decode_pdf_literal(literal[1:-1])
            if value:
                parts.append(value)
        for encoded in _PDF_HEX.findall(match.group(1)):
            try:
                value = bytes.fromhex(encoded.decode("ascii")).decode("utf-8", errors="replace")
            except ValueError:
                continue
            if value:
                parts.append(value)
    return parts


def _decode_pdf_literal(value: bytes) -> str:
    replacements = {
        b"\\n": b"\n",
        b"\\r": b"\r",
        b"\\t": b"\t",
        b"\\b": b"\b",
        b"\\f": b"\f",
        b"\\(": b"(",
        b"\\)": b")",
        b"\\\\": b"\\",
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    value = re.sub(rb"\\([0-7]{1,3})", lambda match: bytes([int(match.group(1), 8)]), value)
    return value.decode("utf-8", errors="replace").strip()


def _pdf_page_count(data: bytes) -> int | None:
    match = re.search(rb"/Count\s+(\d+)", data[: min(len(data), 4 * 1024 * 1024)])
    return int(match.group(1)) if match else None


def _has_message_hint(lines: Sequence[str]) -> bool:
    return any(_TEXT_LINE.match(line) or _SENDER_LINE.match(line) for line in lines)


def _records_from_lines(lines: Sequence[str], *, source_label: str) -> Iterator[NormalizedMessage]:
    pending: dict[str, str] | None = None
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        match = _TEXT_LINE.match(line)
        sender_match = _SENDER_LINE.match(line) if match is None else None
        if match:
            if pending is not None:
                yield _document_record(pending)
            timestamp = match.group("bracket_timestamp") or match.group("bare_timestamp") or "unknown"
            sender = match.group("sender").strip().lstrip("-| ").strip()
            pending = {
                "sender_id": sender,
                "sender_name": sender,
                "content": (match.group("content") or "").strip(),
                "timestamp": timestamp.strip(),
                "message_type": "text",
            }
        elif sender_match:
            if pending is not None:
                yield _document_record(pending)
            sender = sender_match.group("sender").strip()
            pending = {
                "sender_id": sender,
                "sender_name": sender,
                "content": sender_match.group("content").strip(),
                "timestamp": f"{source_label}:{line_number}",
                "message_type": "text",
            }
        elif pending is not None:
            pending["content"] += f"\n{line}"
    if pending is not None:
        yield _document_record(pending)


def _document_record(values: Mapping[str, str]) -> NormalizedMessage:
    try:
        return NormalizedMessage.from_mapping(values)
    except MessageValidationError as exc:
        raise DocumentParserError("invalid_record", str(exc)) from exc


def _local_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.rsplit("}", 1)[-1].rsplit(":", 1)[-1].casefold()
