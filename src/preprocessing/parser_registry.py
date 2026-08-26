"""Content-probed parser contracts for chat import sources."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import io
import json
from itertools import islice
from pathlib import Path
import re
from typing import Any, Iterator, Mapping, Protocol, Sequence
import xml.etree.ElementTree as ET

from src.domain.messages import MessageValidationError, NormalizedMessage
from src.preprocessing.document_parser import DocumentParserError, GenericDocxParser, GenericPdfParser
from src.preprocessing.generic_database import GenericDatabaseError, GenericSqliteParser
from src.preprocessing.qq_backup import QqBackupError, QqBackupParser
from src.preprocessing.qq_database import QqDatabaseError, QqDatabaseParser
from src.preprocessing.wechat_backup import WeChatBackupError, WeChatBackupParser
from src.preprocessing.wechat_database import WeChatDatabaseError, WeChatDatabaseParser


_TIMESTAMP = r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?"
_TEXT_LINE = re.compile(
    rf"^(?:\[(?P<bracket_timestamp>[^\]]+)\]|(?P<bare_timestamp>{_TIMESTAMP}))"
    r"\s*(?:[-|]\s*)?(?P<sender>[^:：]{1,128})\s*[:：]\s*(?P<content>.*)$"
)
_SENDER_LINE = re.compile(r"^(?P<sender>[^:：]{1,128})\s*[:：]\s*(?P<content>.+)$")
_WECHAT_TEXT_LINE = re.compile(
    rf"^(?P<timestamp>{_TIMESTAMP})\s+(?P<sender>[^:：]{{1,128}})"
    r"(?:\s*[:：]\s*(?P<content>.*))?$"
)
_PROBE_BYTES = 64 * 1024
# Standard-library JSON decoding materializes one document. Large imports remain
# supported through JSONL/text/HTML/XML streaming parsers; this cap prevents a
# 3 GiB single JSON export from bypassing the training path's memory bound.
MAX_EAGER_JSON_BYTES = 64 * 1024**2
MAX_TRAINING_STREAM_RECORD_BYTES = 64 * 1024
_DATABASE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})


class ParserError(ValueError):
    """Actionable parser failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ParserSource:
    path: Path
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ParserProbe:
    source_type: str
    confidence: float
    supported: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ParserValidation:
    valid: bool
    code: str = "ok"
    message: str = ""


@dataclass(frozen=True, slots=True)
class ParseResult:
    source_type: str
    records: tuple[NormalizedMessage, ...]
    warnings: tuple[str, ...]
    summary: Mapping[str, Any]


class ParserPlugin(Protocol):
    source_type: str

    def probe(self, source: ParserSource) -> ParserProbe:
        """Return content-signature support and confidence."""

    def validate(self, source: ParserSource) -> ParserValidation:
        """Validate the source before streaming records."""

    def stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        """Yield canonical records without requiring the registry to buffer them."""

    def summarize(
        self,
        records: Sequence[NormalizedMessage],
        warnings: Sequence[str],
        confidence: float,
    ) -> Mapping[str, Any]:
        """Summarize output and parser confidence."""


class ParserRegistry:
    """Select and run parsers by content signature and optional metadata."""

    def __init__(self, parsers: Sequence[ParserPlugin] = ()) -> None:
        self._parsers: dict[str, ParserPlugin] = {}
        for parser in parsers:
            self.register(parser)

    @classmethod
    def with_builtins(cls) -> "ParserRegistry":
        return cls(
            (
                GenericJsonParser(),
                GenericJsonLinesParser(),
                WeChatDatabaseParser(),
                QqDatabaseParser(),
                WeChatBackupParser(),
                QqBackupParser(),
                WeChatHtmlParser(),
                WeChatTextParser(),
                QqHtmlParser(),
                QqTextParser(),
                GenericSqliteParser(),
                GenericDocxParser(),
                GenericPdfParser(),
                GenericHtmlParser(),
                GenericXmlParser(),
                GenericCsvParser(),
                GenericTextParser(),
            )
        )

    def register(self, parser: ParserPlugin) -> None:
        if not parser.source_type.strip():
            raise ValueError("parser source_type is required")
        if parser.source_type in self._parsers:
            raise ValueError(f"parser source_type already registered: {parser.source_type}")
        self._parsers[parser.source_type] = parser

    def probes(self, path: str | Path, metadata: Mapping[str, Any] | None = None) -> tuple[ParserProbe, ...]:
        source = self._source(path, metadata)
        return tuple(parser.probe(source) for parser in self._parsers.values())

    def select(self, path: str | Path, metadata: Mapping[str, Any] | None = None) -> ParserPlugin:
        source = self._source(path, metadata)
        requested_type = source.metadata.get("source_type")
        if isinstance(requested_type, str) and requested_type:
            parser = self._parsers.get(requested_type)
            if parser is None:
                raise ParserError("unsupported_format", f"no parser is registered for {requested_type}")
            return parser

        candidates = [
            (parser.probe(source), parser)
            for parser in self._parsers.values()
        ]
        supported = [item for item in candidates if item[0].supported and item[0].confidence > 0]
        if not supported:
            raise ParserError("unsupported_format", "no parser can recognize this source")
        return max(supported, key=lambda item: item[0].confidence)[1]

    def parse(
        self,
        path: str | Path,
        metadata: Mapping[str, Any] | None = None,
        *,
        max_records: int | None = None,
    ) -> ParseResult:
        source = self._source(path, metadata)
        parser = self.select(source.path, source.metadata)
        validation = parser.validate(source)
        if not validation.valid:
            raise ParserError(validation.code, validation.message or "source validation failed")

        probe = parser.probe(source)
        if max_records is not None and (
            isinstance(max_records, bool) or not isinstance(max_records, int) or max_records <= 0
        ):
            raise ParserError("invalid_preview_limit", "max_records must be a positive integer")
        try:
            stream = parser.stream_records(source)
            if max_records is None:
                records = tuple(stream)
                truncated = False
            else:
                records = tuple(islice(stream, max_records))
                truncated = next(stream, None) is not None
        except ParserError:
            raise
        except MessageValidationError as exc:
            raise ParserError("invalid_record", str(exc)) from exc
        except WeChatDatabaseError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        except QqDatabaseError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        except WeChatBackupError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        except QqBackupError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        except GenericDatabaseError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        except DocumentParserError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        records = _assign_record_ids(records, source, parser.source_type)
        if not records:
            raise ParserError("empty_source", "parser produced no records")
        warnings: tuple[str, ...] = ()
        summary = dict(parser.summarize(records, warnings, probe.confidence))
        summary["truncated"] = truncated
        return ParseResult(
            source_type=parser.source_type,
            records=records,
            warnings=warnings,
            summary=summary,
        )

    def iter_records(
        self,
        path: str | Path,
        metadata: Mapping[str, Any] | None = None,
    ) -> Iterator[NormalizedMessage]:
        """Stream validated records with the same stable IDs used by preview parsing.

        The eager ``parse`` API intentionally builds a tuple for previews and parser
        summaries. Training paths must not use that API because a completed import
        can be several GiB; this method preserves the parser's validation and error
        normalization while keeping only the current record in memory.
        """

        source = self._source(path, metadata)
        parser = self.select(source.path, source.metadata)
        validation = parser.validate(source)
        if not validation.valid:
            raise ParserError(validation.code, validation.message or "source validation failed")

        namespace = _record_id_namespace(source)
        yielded = False
        try:
            for index, record in enumerate(parser.stream_records(source)):
                yielded = True
                yield record.with_record_id(_stable_record_id(namespace, parser.source_type, index))
        except ParserError:
            raise
        except MessageValidationError as exc:
            raise ParserError("invalid_record", str(exc)) from exc
        except WeChatDatabaseError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        except QqDatabaseError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        except WeChatBackupError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        except QqBackupError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        except GenericDatabaseError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        except DocumentParserError as exc:
            raise ParserError(exc.code, str(exc)) from exc
        if not yielded:
            raise ParserError("empty_source", "parser produced no records")

    @staticmethod
    def _source(path: str | Path, metadata: Mapping[str, Any] | None) -> ParserSource:
        resolved = Path(path)
        normalized_metadata = dict(metadata or {})
        if not resolved.exists():
            raise ParserError("source_not_found", "parser source does not exist")
        if (
            resolved.is_file()
            and resolved.suffix.casefold() in _DATABASE_SUFFIXES
            and not normalized_metadata.get("source_type")
        ):
            raise ParserError(
                "source_not_directory",
                "数据库解析必须提供包含 .db 文件的目录，不能直接上传单个数据库文件",
            )
        if resolved.is_dir() and normalized_metadata.get("source_type") not in {
            "wechat_database",
            "qq_database",
        }:
            # Named platform database directories may still be auto-probed.
            normalized_name = resolved.name.casefold()
            is_named_database = normalized_name in {
                "db_storage",
                "msg",
                "wechat",
                "wechat_db",
                "qq",
                "qq_db",
                "qq_database",
                "qqdata",
                "qq_storage",
            } or "qq" in normalized_name or "群" in resolved.name
            if not is_named_database and normalized_metadata.get("source_type") != "generic_sqlite":
                try:
                    has_database_file = any(
                        candidate.is_file() and candidate.suffix.casefold() in _DATABASE_SUFFIXES
                        for candidate in resolved.rglob("*")
                    )
                except OSError:
                    has_database_file = False
                if has_database_file:
                    return ParserSource(resolved, normalized_metadata)
                raise ParserError("source_not_file", "parser source must be a file unless it is a named platform database directory")
        return ParserSource(resolved, normalized_metadata)


class _BaseParser:
    source_type = ""

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
        }


class GenericTextParser(_BaseParser):
    source_type = "generic_text"

    def probe(self, source: ParserSource) -> ParserProbe:
        sample = _read_text_sample(source.path)
        if sample is None or "\x00" in sample:
            return ParserProbe(self.source_type, 0.0, False, "source is not valid UTF-8 text")
        non_empty = [line for line in sample.splitlines() if line.strip()]
        if not non_empty:
            return ParserProbe(self.source_type, 0.0, False, "source is empty")
        matched = sum(
            1
            for line in non_empty
            if _TEXT_LINE.match(line.strip()) or _SENDER_LINE.match(line.strip())
        )
        confidence = 0.92 if matched else 0.55
        return ParserProbe(self.source_type, confidence, True, "supported text source")

    def validate(self, source: ParserSource) -> ParserValidation:
        probe = self.probe(source)
        if not probe.supported:
            return ParserValidation(False, "unsupported_format", probe.reason)
        return ParserValidation(True)

    def stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        encoding = _detect_text_encoding(source.path)
        if encoding is None:
            raise ParserError("unsupported_format", "source is not a supported text encoding")

        max_record_bytes = _stream_record_byte_limit(source)
        pending: dict[str, str] | None = None
        for line_number, raw_line in _iter_text_lines(
            source.path,
            encoding,
            max_record_bytes=max_record_bytes,
        ):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            stripped = line.strip()
            match = _TEXT_LINE.match(stripped)
            sender_match = _SENDER_LINE.match(stripped) if match is None else None
            if match:
                if pending is not None:
                    yield _text_record(pending)
                timestamp = match.group("bracket_timestamp") or match.group("bare_timestamp")
                content = match.group("content").strip()
                _require_stream_record_bytes(content, max_record_bytes, line_number)
                pending = {
                    "sender_id": match.group("sender").strip().lstrip("-| ").strip(),
                    "sender_name": match.group("sender").strip().lstrip("-| ").strip(),
                    "content": content,
                    "timestamp": timestamp.strip(),
                    "message_type": "text",
                }
            elif sender_match:
                if pending is not None:
                    yield _text_record(pending)
                content = sender_match.group("content").strip()
                _require_stream_record_bytes(content, max_record_bytes, line_number)
                values = {
                    "sender_id": sender_match.group("sender").strip(),
                    "sender_name": sender_match.group("sender").strip(),
                    "content": content,
                    "timestamp": f"line:{line_number}",
                    "message_type": "text",
                }
                pending = values
            elif pending is not None:
                continuation = stripped
                if continuation:
                    _append_stream_text_continuation(
                        pending,
                        continuation,
                        max_record_bytes=max_record_bytes,
                        line_number=line_number,
                    )
            else:
                _require_stream_record_bytes(stripped, max_record_bytes, line_number)
                pending = {
                    "sender_id": "unknown",
                    "sender_name": "unknown",
                    "content": stripped,
                    "timestamp": f"line:{line_number}",
                    "message_type": "text",
                }
        if pending is not None:
            yield _text_record(pending)


class WeChatTextParser(GenericTextParser):
    """Parse common WeChat text exports with timestamped sender blocks."""

    source_type = "wechat_text"
    _platform_name = "WeChat"
    _marker_source_types = frozenset({"wechat_text", "wechat_html"})
    _marker_labels = ("微信", "wechat", "weixin", "micro-msg")
    _sample_markers = ("微信", "wechat", "weixin", "wxid_")
    _header_markers = ("微信聊天记录", "wechat chat", "wechat export")

    def probe(self, source: ParserSource) -> ParserProbe:
        sample = _read_text_sample(source.path)
        if sample is None or "\x00" in sample:
            return ParserProbe(self.source_type, 0.0, False, "source is not valid text")
        if _looks_like_json_sample(sample):
            return ParserProbe(self.source_type, 0.0, False, "source is JSON")
        lines = [line.strip() for line in sample.splitlines() if line.strip()]
        matched = sum(
            1
            for line in lines
            if _WECHAT_TEXT_LINE.match(line) or _TEXT_LINE.match(line)
        )
        explicit = source.metadata.get("source_type") == self.source_type
        if not matched and not explicit:
            return ParserProbe(self.source_type, 0.0, False, "no timestamped WeChat messages found")
        marker = _platform_marker(
            source,
            sample,
            source_types=self._marker_source_types,
            labels=self._marker_labels,
            sample_markers=self._sample_markers,
        )
        if not marker and not explicit:
            return ParserProbe(
                self.source_type,
                0.0,
                False,
                f"source has no {self._platform_name} signature",
            )
        confidence = 0.99 if explicit or marker == "explicit" else 0.96
        return ParserProbe(
            self.source_type,
            confidence,
            True,
            f"{self._platform_name} text export recognized",
        )

    def stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        encoding = _detect_text_encoding(source.path)
        if encoding is None:
            raise ParserError("unsupported_format", "source is not a supported text encoding")

        max_record_bytes = _stream_record_byte_limit(source)
        pending: dict[str, str] | None = None
        for line_number, raw_line in _iter_text_lines(
            source.path,
            encoding,
            max_record_bytes=max_record_bytes,
        ):
            line = raw_line.rstrip("\r\n")
            if not line.strip():
                continue
            stripped = line.strip()
            wechat_match = _WECHAT_TEXT_LINE.match(stripped)
            generic_match = _TEXT_LINE.match(stripped)
            sender_match = (
                _SENDER_LINE.match(stripped)
                if wechat_match is None and generic_match is None
                else None
            )
            if wechat_match or generic_match:
                if pending is not None:
                    yield _text_record(pending)
                if wechat_match:
                    timestamp = wechat_match.group("timestamp")
                    sender = wechat_match.group("sender").strip().lstrip("-| ").strip()
                    content = wechat_match.group("content") or ""
                else:
                    timestamp = generic_match.group("bracket_timestamp") or generic_match.group(
                        "bare_timestamp"
                    )
                    sender = generic_match.group("sender").strip().lstrip("-| ").strip()
                    content = generic_match.group("content")
                content = content.strip()
                _require_stream_record_bytes(content, max_record_bytes, line_number)
                pending = {
                    "sender_id": sender,
                    "sender_name": sender,
                    "content": content,
                    "timestamp": timestamp.strip(),
                    "message_type": "text",
                }
            elif sender_match:
                if pending is not None:
                    yield _text_record(pending)
                sender = sender_match.group("sender").strip()
                content = sender_match.group("content").strip()
                _require_stream_record_bytes(content, max_record_bytes, line_number)
                pending = {
                    "sender_id": sender,
                    "sender_name": sender,
                    "content": content,
                    "timestamp": f"line:{line_number}",
                    "message_type": "text",
                }
            elif pending is not None:
                _append_stream_text_continuation(
                    pending,
                    stripped,
                    max_record_bytes=max_record_bytes,
                    line_number=line_number,
                )
            elif not self._is_platform_header(stripped):
                _require_stream_record_bytes(stripped, max_record_bytes, line_number)
                pending = {
                    "sender_id": "unknown",
                    "sender_name": "unknown",
                    "content": stripped,
                    "timestamp": f"line:{line_number}",
                    "message_type": "text",
                }
        if pending is not None:
            yield _text_record(pending)

    def _is_platform_header(self, line: str) -> bool:
        normalized = line.strip().lower()
        return any(marker in normalized for marker in self._header_markers)


class QqTextParser(WeChatTextParser):
    """Parse common QQ text exports with timestamped sender blocks."""

    source_type = "qq_text"
    _platform_name = "QQ"
    _marker_source_types = frozenset({"qq_text", "qq_html"})
    _marker_labels = ("qq", "qq群", "qq聊天", "qq chat", "qq export")
    _sample_markers = ("qq", "qq群", "qq聊天", "qq chat", "qq export", "qq_")
    _header_markers = ("qq聊天记录", "qq chat", "qq export", "qq群")


@dataclass(slots=True)
class _HtmlMessageContext:
    tag: str
    depth: int
    sender_id: str | None = None
    sender_name: str | None = None
    timestamp: str | None = None
    message_type: str = "text"
    content_parts: list[str] | None = None
    active_fields: list[tuple[str, int]] | None = None
    attachments: list[dict[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.content_parts is None:
            self.content_parts = []
        if self.active_fields is None:
            self.active_fields = []
        if self.attachments is None:
            self.attachments = []


class _WeChatHTMLDocumentParser(HTMLParser):
    _FIELD_NAMES = {
        "sender": "sender",
        "sender-id": "sender",
        "sender_name": "sender",
        "sender-name": "sender",
        "nickname": "sender",
        "name": "sender",
        "from": "sender",
        "time": "timestamp",
        "timestamp": "timestamp",
        "date": "timestamp",
        "content": "content",
        "message-content": "content",
        "text": "content",
        "message-text": "content",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.contexts: list[_HtmlMessageContext] = []
        self.records: list[NormalizedMessage] = []
        self._ignored_depths: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.depth += 1
        normalized = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() in {"script", "style", "template"}:
            self._ignored_depths.append(self.depth)
            return
        if self._ignored_depths:
            return
        if tag.lower() == "br" and self.contexts:
            self.contexts[-1].content_parts.append("\n")
            return
        if self._is_message_container(normalized):
            context = _HtmlMessageContext(tag.lower(), self.depth)
            (
                context.sender_id,
                context.sender_name,
                context.timestamp,
                context.message_type,
            ) = self._container_fields(normalized)
            self.contexts.append(context)
            return
        if self.contexts:
            attachment = _html_attachment(tag, normalized)
            if attachment is not None:
                self.contexts[-1].attachments.append(attachment)
            field = self._field_for_element(tag, normalized)
            if field:
                self.contexts[-1].active_fields.append((field, self.depth))
            context = self.contexts[-1]
            if tag.lower() == "time" and not context.timestamp:
                context.timestamp = normalized.get("datetime") or normalized.get("data-time") or None

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if self._ignored_depths and self._ignored_depths[-1] == self.depth:
            self._ignored_depths.pop()
        elif not self._ignored_depths and self.contexts:
            context = self.contexts[-1]
            if context.tag == normalized_tag and context.depth == self.depth:
                self._finalize_context()
            else:
                context.active_fields[:] = [
                    item for item in context.active_fields if item[1] != self.depth
                ]
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data: str) -> None:
        if self._ignored_depths or not self.contexts or not data:
            return
        context = self.contexts[-1]
        field = context.active_fields[-1][0] if context.active_fields else "content"
        text = data.strip() if field != "content" else data
        if not text:
            return
        if field == "sender":
            if context.sender_name is None:
                context.sender_name = text
            if not context.sender_id:
                context.sender_id = context.sender_name
        elif field == "timestamp":
            if context.timestamp is None:
                context.timestamp = text
        else:
            context.content_parts.append(text)

    def finish(self) -> None:
        while self.contexts:
            self._finalize_context()

    def drain(self) -> tuple[NormalizedMessage, ...]:
        records = tuple(self.records)
        self.records.clear()
        return records

    def _is_message_container(self, attrs: Mapping[str, str]) -> bool:
        classes = set(re.split(r"\s+", attrs.get("class", "").lower()))
        return bool(
            classes.intersection({"message", "msg", "chat-message", "message-item", "record"})
            or attrs.get("data-message-id")
            or attrs.get("data-sender-id")
            or attrs.get("data-sender")
        )

    def _container_fields(
        self,
        attrs: Mapping[str, str],
    ) -> tuple[str | None, str | None, str | None, str]:
        return (
            _first_attr(attrs, "data-sender-id", "data-sender", "data-from"),
            _first_attr(attrs, "data-sender-name", "data-nickname", "data-name"),
            _first_attr(attrs, "data-timestamp", "data-time", "datetime"),
            _first_attr(attrs, "data-message-type", "data-type") or "text",
        )

    def _field_for_element(self, tag: str, attrs: Mapping[str, str]) -> str | None:
        if tag == "time":
            return "timestamp"
        values = [attrs.get("id", ""), attrs.get("class", "")]
        for value in values:
            for token in re.split(r"\s+", value.lower()):
                if token in self._FIELD_NAMES:
                    return self._FIELD_NAMES[token]
        return None

    def _finalize_context(self) -> None:
        context = self.contexts.pop()
        content = _normalize_html_text("".join(context.content_parts))
        if not content and not context.attachments:
            return
        sender_id = (context.sender_id or context.sender_name or "unknown").strip()
        sender_name = (context.sender_name or sender_id).strip()
        timestamp = (context.timestamp or f"html:{len(self.records) + 1}").strip()
        self.records.append(
            _text_record(
                {
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                    "content": content,
                    "timestamp": timestamp,
                    "message_type": context.message_type,
                    "attachments": context.attachments,
                }
            )
        )


class WeChatHtmlParser(_BaseParser):
    """Parse WeChat HTML exports using message containers and data attributes."""

    source_type = "wechat_html"
    _platform_name = "WeChat"
    _marker_source_types = frozenset({"wechat_text", "wechat_html"})
    _marker_labels = ("微信", "wechat", "weixin", "micro-msg")
    _sample_markers = ("微信", "wechat", "weixin", "wxid_")

    def probe(self, source: ParserSource) -> ParserProbe:
        sample = _read_text_sample(source.path)
        if sample is None or "\x00" in sample:
            return ParserProbe(self.source_type, 0.0, False, "source is not valid HTML text")
        lower = sample.lower()
        html_hint = "<html" in lower or "<!doctype html" in lower or "<body" in lower
        message_hint = bool(
            re.search(r'class\s*=\s*[\"\'][^\"\']*\b(message|msg)\b', lower)
            or "data-sender-id" in lower
            or "data-message-id" in lower
        )
        explicit = source.metadata.get("source_type") == self.source_type
        if not html_hint or (not message_hint and not explicit):
            return ParserProbe(
                self.source_type,
                0.0,
                False,
                f"no {self._platform_name} HTML message signature found",
            )
        marker = _platform_marker(
            source,
            sample,
            source_types=self._marker_source_types,
            labels=self._marker_labels,
            sample_markers=self._sample_markers,
        )
        if not marker and not explicit:
            return ParserProbe(
                self.source_type,
                0.0,
                False,
                f"source has no {self._platform_name} signature",
            )
        confidence = 0.99 if explicit or marker == "explicit" else 0.97
        return ParserProbe(
            self.source_type,
            confidence,
            True,
            f"{self._platform_name} HTML export recognized",
        )

    def validate(self, source: ParserSource) -> ParserValidation:
        probe = self.probe(source)
        if not probe.supported:
            return ParserValidation(False, "unsupported_format", probe.reason)
        return ParserValidation(True)

    def stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        encoding = _detect_text_encoding(source.path)
        if encoding is None:
            raise ParserError("unsupported_format", "source is not a supported text encoding")
        parser = _WeChatHTMLDocumentParser()
        with source.path.open("r", encoding=encoding) as handle:
            for chunk in iter(lambda: handle.read(_PROBE_BYTES), ""):
                parser.feed(chunk)
                yield from parser.drain()
        parser.close()
        parser.finish()
        yield from parser.drain()


class QqHtmlParser(WeChatHtmlParser):
    """Parse common QQ HTML exports using message containers and data attributes."""

    source_type = "qq_html"
    _platform_name = "QQ"
    _marker_source_types = frozenset({"qq_text", "qq_html"})
    _marker_labels = ("qq", "qq群", "qq聊天", "qq chat", "qq export")
    _sample_markers = ("qq", "qq群", "qq聊天", "qq chat", "qq export", "qq_")


class _GenericHTMLDocumentParser(_WeChatHTMLDocumentParser):
    _MESSAGE_CLASSES = frozenset(
        {
            "message",
            "msg",
            "chat-message",
            "message-item",
            "chat-item",
            "chat-entry",
            "conversation-message",
            "record",
            "entry",
            "utterance",
            "bubble",
        }
    )
    _FIELD_NAMES = {
        "sender": "sender",
        "sender-id": "sender",
        "sender_id": "sender",
        "sender-name": "sender",
        "sender_name": "sender",
        "author": "sender",
        "author-id": "sender",
        "author_id": "sender",
        "author-name": "sender",
        "author_name": "sender",
        "from": "sender",
        "speaker": "sender",
        "user": "sender",
        "username": "sender",
        "nickname": "sender",
        "name": "sender",
        "time": "timestamp",
        "timestamp": "timestamp",
        "datetime": "timestamp",
        "date": "timestamp",
        "created-at": "timestamp",
        "created_at": "timestamp",
        "content": "content",
        "message": "content",
        "message-content": "content",
        "message_content": "content",
        "message-text": "content",
        "message_text": "content",
        "text": "content",
        "body": "content",
    }

    def _is_message_container(self, attrs: Mapping[str, str]) -> bool:
        classes = {
            token.casefold()
            for token in re.split(r"\s+", attrs.get("class", ""))
            if token
        }
        return bool(
            classes.intersection(self._MESSAGE_CLASSES)
            or any(
                attrs.get(name)
                for name in (
                    "data-message-id",
                    "data-sender-id",
                    "data-sender",
                    "data-author-id",
                    "data-author",
                    "data-speaker",
                    "data-user-id",
                    "data-timestamp",
                    "data-time",
                )
            )
        )

    def _container_fields(
        self,
        attrs: Mapping[str, str],
    ) -> tuple[str | None, str | None, str | None, str]:
        return (
            _first_attr(
                attrs,
                "data-sender-id",
                "data-author-id",
                "data-user-id",
                "data-speaker-id",
                "data-sender",
                "data-author",
                "data-speaker",
                "data-user",
                "data-from",
            ),
            _first_attr(
                attrs,
                "data-sender-name",
                "data-author-name",
                "data-nickname",
                "data-display-name",
                "data-name",
            ),
            _first_attr(
                attrs,
                "data-timestamp",
                "data-time",
                "data-date",
                "datetime",
            ),
            _first_attr(attrs, "data-message-type", "data-type", "data-kind") or "text",
        )

    def _field_for_element(self, tag: str, attrs: Mapping[str, str]) -> str | None:
        if tag.casefold() == "time":
            return "timestamp"
        values = [
            attrs.get("id", ""),
            attrs.get("class", ""),
            attrs.get("data-field", ""),
            attrs.get("data-role", ""),
            attrs.get("aria-label", ""),
        ]
        for value in values:
            for token in re.split(r"\s+", value.casefold().replace(".", " ")):
                if token in self._FIELD_NAMES:
                    return self._FIELD_NAMES[token]
        return None


class GenericHtmlParser(_BaseParser):
    """Parse common HTML conversation exports into canonical messages."""

    source_type = "generic_html"

    def probe(self, source: ParserSource) -> ParserProbe:
        sample = _read_text_sample(source.path)
        if sample is None or "\x00" in sample:
            return ParserProbe(self.source_type, 0.0, False, "source is not valid HTML text")
        if not _looks_like_html_sample(sample):
            return ParserProbe(self.source_type, 0.0, False, "HTML document structure not recognized")
        explicit = source.metadata.get("source_type") == self.source_type
        message_hint = _generic_html_message_hint(sample)
        if not message_hint and not explicit:
            if source.path.suffix.casefold() in {".html", ".htm"}:
                return ParserProbe(
                    self.source_type,
                    0.8,
                    True,
                    "HTML document recognized but no message container signature found",
                )
            return ParserProbe(self.source_type, 0.0, False, "HTML message container not recognized")
        platform_marker = _platform_marker(
            source,
            sample,
            source_types=frozenset({"wechat_html", "qq_html"}),
            labels=("微信", "wechat", "weixin", "qq", "qq群", "qq聊天", "qq chat"),
            sample_markers=("微信", "wechat", "weixin", "wxid_", "qq", "qq群", "qq_"),
        )
        if platform_marker and not explicit:
            return ParserProbe(
                self.source_type,
                0.9,
                True,
                "generic HTML structure recognized as a platform export fallback",
            )
        return ParserProbe(
            self.source_type,
            0.98 if message_hint else 0.8,
            True,
            "generic HTML conversation structure recognized",
        )

    def validate(self, source: ParserSource) -> ParserValidation:
        probe = self.probe(source)
        if not probe.supported:
            return ParserValidation(False, "unsupported_format", probe.reason)
        try:
            record_count = sum(1 for _ in self._stream_records(source))
        except ParserError as exc:
            return ParserValidation(False, exc.code, str(exc))
        if record_count == 0:
            return ParserValidation(False, "unsupported_format", "HTML document contains no message records")
        return ParserValidation(True)

    def stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        yield from self._stream_records(source)

    def _stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        encoding = _detect_text_encoding(source.path)
        if encoding is None:
            raise ParserError("unsupported_format", "source is not a supported text encoding")
        parser = _GenericHTMLDocumentParser()
        try:
            with source.path.open("r", encoding=encoding) as handle:
                for chunk in iter(lambda: handle.read(_PROBE_BYTES), ""):
                    parser.feed(chunk)
                    yield from parser.drain()
            parser.close()
            parser.finish()
            yield from parser.drain()
        except (OSError, UnicodeError) as exc:
            raise ParserError("unsupported_format", f"HTML source could not be read: {exc}") from exc


class GenericJsonParser(_BaseParser):
    source_type = "generic_json"

    def probe(self, source: ParserSource) -> ParserProbe:
        if _requires_streaming_json(source.path):
            sample = _read_text_sample(source.path)
            if sample is not None and _looks_like_json_sample(sample):
                if _looks_like_jsonl_messages(sample):
                    # A large JSONL export begins with ``{`` too. Let its streaming
                    # parser win instead of classifying it as one eager document.
                    return ParserProbe(
                        self.source_type,
                        0.0,
                        False,
                        "large JSONL message records are handled by the streaming parser",
                    )
                return ParserProbe(
                    self.source_type,
                    0.99,
                    True,
                    "large JSON conversation requires a streaming export format",
                )
        try:
            data = _load_json(source.path)
            _message_items(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ParserError) as exc:
            code = exc.code if isinstance(exc, ParserError) else "unsupported_format"
            reason = str(exc) or "source is not a supported JSON conversation"
            return ParserProbe(self.source_type, 0.0, False, f"{code}: {reason}")
        return ParserProbe(self.source_type, 0.98, True, "JSON conversation structure recognized")

    def validate(self, source: ParserSource) -> ParserValidation:
        if _requires_streaming_json(source.path):
            return ParserValidation(
                False,
                "streaming_format_required",
                "large JSON imports require JSONL or another streaming conversation export",
            )
        try:
            data = _load_json(source.path)
            items = _message_items(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ParserError) as exc:
            code = exc.code if isinstance(exc, ParserError) else "unsupported_format"
            return ParserValidation(False, code, str(exc))
        if not items:
            return ParserValidation(False, "empty_source", "JSON conversation contains no messages")
        return ParserValidation(True)

    def stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        data = _load_json(source.path)
        for index, value in enumerate(_message_items(data), 1):
            try:
                yield NormalizedMessage.from_mapping(value)
            except (MessageValidationError, TypeError) as exc:
                raise ParserError("invalid_record", f"JSON message {index}: {exc}") from exc


class GenericJsonLinesParser(_BaseParser):
    source_type = "generic_jsonl"

    def probe(self, source: ParserSource) -> ParserProbe:
        sample = _read_text_sample(source.path)
        if sample is None:
            return ParserProbe(self.source_type, 0.0, False, "source is not valid UTF-8 text")
        lines = _complete_jsonl_sample_lines(
            sample,
            final_line_complete=_probe_covers_source(source.path),
        )
        if not lines:
            return ParserProbe(self.source_type, 0.0, False, "source is empty")
        valid = 0
        for line in lines[:32]:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, Mapping) and _looks_like_message(value):
                valid += 1
        confidence = 0.96 if valid == len(lines[:32]) else 0.0
        return ParserProbe(self.source_type, confidence, confidence > 0, "JSONL message records recognized")

    def validate(self, source: ParserSource) -> ParserValidation:
        probe = self.probe(source)
        if not probe.supported:
            return ParserValidation(False, "unsupported_format", probe.reason)
        return ParserValidation(True)

    def stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        encoding = _detect_text_encoding(source.path)
        if encoding is None:
            raise ParserError("unsupported_format", "source is not a supported text encoding")
        max_record_bytes = _stream_record_byte_limit(source)
        for line_number, raw_line in _iter_text_lines(
            source.path,
            encoding,
            max_record_bytes=max_record_bytes,
        ):
            line = raw_line.strip()
            if not line:
                continue
            _require_stream_record_bytes(line, max_record_bytes, line_number)
            try:
                value = json.loads(line)
                if not isinstance(value, Mapping):
                    raise TypeError("JSONL record must be an object")
                yield NormalizedMessage.from_mapping(value)
            except (json.JSONDecodeError, MessageValidationError, TypeError) as exc:
                raise ParserError("invalid_record", f"JSONL line {line_number}: {exc}") from exc


class GenericXmlParser(_BaseParser):
    """Parse generic XML conversation records without buffering the document."""

    source_type = "generic_xml"

    def probe(self, source: ParserSource) -> ParserProbe:
        sample = _read_text_sample(source.path)
        if sample is None or "\x00" in sample:
            return ParserProbe(self.source_type, 0.0, False, "source is not valid text")
        explicit = source.metadata.get("source_type") == self.source_type
        xml_like = _looks_like_xml_sample(sample)
        extension_hint = source.path.suffix.casefold() == ".xml"
        if _xml_security_violation(sample):
            if xml_like or extension_hint or explicit:
                return ParserProbe(
                    self.source_type,
                    0.9,
                    True,
                    "XML DOCTYPE/ENTITY declarations are not supported",
                )
            return ParserProbe(self.source_type, 0.0, False, "XML security declaration not recognized")
        if _xml_message_hint(sample):
            return ParserProbe(self.source_type, 0.97, True, "XML conversation message elements recognized")
        if xml_like:
            return ParserProbe(self.source_type, 0.8, True, "XML document structure recognized")
        if extension_hint or explicit:
            return ParserProbe(self.source_type, 0.8, True, "XML source selected by extension or metadata")
        return ParserProbe(self.source_type, 0.0, False, "XML conversation message elements not recognized")

    def validate(self, source: ParserSource) -> ParserValidation:
        probe = self.probe(source)
        if not probe.supported:
            return ParserValidation(False, "unsupported_format", probe.reason)
        sample = _read_text_sample(source.path)
        if sample is None:
            return ParserValidation(False, "unsupported_format", "source is not a supported text encoding")
        if _xml_security_violation(sample):
            return ParserValidation(
                False,
                "unsupported_format",
                "XML DOCTYPE/ENTITY declarations are not supported",
            )
        if not _looks_like_xml_sample(sample) and not (
            source.path.suffix.casefold() == ".xml"
            or source.metadata.get("source_type") == self.source_type
        ):
            return ParserValidation(False, "invalid_record", "XML document must start with an element")
        try:
            record_count = sum(1 for _ in _iter_xml_records(source.path))
        except ParserError as exc:
            return ParserValidation(False, exc.code, str(exc))
        if record_count == 0:
            return ParserValidation(False, "unsupported_format", "XML document contains no message records")
        return ParserValidation(True)

    def stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        yield from _iter_xml_records(source.path)


class GenericCsvParser(_BaseParser):
    """Parse delimited conversation exports into canonical messages."""

    source_type = "generic_csv"

    def probe(self, source: ParserSource) -> ParserProbe:
        sample = _read_text_sample(source.path)
        if sample is None or "\x00" in sample:
            return ParserProbe(self.source_type, 0.0, False, "source is not valid text")
        header_info = _csv_header_info(sample)
        if header_info is None:
            return ParserProbe(self.source_type, 0.0, False, "CSV header could not be detected")
        headers, _ = header_info
        header_map = _csv_header_map(headers)
        if _csv_has_required_headers(header_map):
            return ParserProbe(self.source_type, 0.97, True, "CSV conversation headers recognized")
        if (
            source.path.suffix.casefold() == ".csv"
            or source.metadata.get("source_type") == self.source_type
        ):
            return ParserProbe(
                self.source_type,
                0.8,
                True,
                "CSV structure recognized but required conversation headers are missing",
            )
        return ParserProbe(self.source_type, 0.0, False, "CSV conversation headers not recognized")

    def validate(self, source: ParserSource) -> ParserValidation:
        probe = self.probe(source)
        if not probe.supported:
            return ParserValidation(False, "unsupported_format", probe.reason)
        header_info = _csv_header_info(_read_text_sample(source.path) or "")
        if header_info is None:
            return ParserValidation(False, "unsupported_format", "CSV header could not be detected")
        headers, _ = header_info
        header_map = _csv_header_map(headers)
        if not _csv_has_required_headers(header_map):
            return ParserValidation(
                False,
                "unsupported_format",
                "CSV must include sender, content, and timestamp columns",
            )
        return ParserValidation(True)

    def stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        encoding = _detect_text_encoding(source.path)
        if encoding is None:
            raise ParserError("unsupported_format", "source is not a supported text encoding")
        sample = _read_text_sample(source.path)
        header_info = _csv_header_info(sample or "")
        if header_info is None:
            raise ParserError("unsupported_format", "CSV header could not be detected")
        _, dialect = header_info
        try:
            with source.path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle, dialect=dialect, strict=True)
                header_map = _csv_header_map(reader.fieldnames or ())
                if not _csv_has_required_headers(header_map):
                    raise ParserError(
                        "unsupported_format",
                        "CSV must include sender, content, and timestamp columns",
                    )
                for row_number, row in enumerate(reader, 2):
                    if None in row:
                        raise ParserError(
                            "invalid_record",
                            f"CSV row {row_number} has more fields than its header",
                        )
                    values = _csv_record_values(row, header_map)
                    try:
                        yield NormalizedMessage.from_mapping(values)
                    except MessageValidationError as exc:
                        raise ParserError("invalid_record", f"CSV row {row_number}: {exc}") from exc
        except csv.Error as exc:
            raise ParserError("invalid_record", f"CSV parsing failed: {exc}") from exc


_XML_MESSAGE_TAGS = frozenset({"message", "msg", "record", "item", "entry", "chat", "utterance"})
_XML_FIELD_ALIASES: dict[str, frozenset[str]] = {
    "sender_id": frozenset(
        {
            "sender",
            "sender_id",
            "senderid",
            "from",
            "from_id",
            "speaker",
            "author",
            "user",
            "user_id",
            "uid",
        }
    ),
    "sender_name": frozenset({"sender_name", "sendername", "nickname", "display_name"}),
    "content": frozenset({"content", "message", "message_text", "msg", "text", "body"}),
    "timestamp": frozenset({"timestamp", "time", "datetime", "date", "created_at", "created"}),
    "message_type": frozenset({"message_type", "type", "kind"}),
}
_XML_ATTACHMENT_TAGS = frozenset({"attachment", "media", "image", "audio", "video", "file", "document", "sticker"})


def _iter_xml_records(path: Path) -> Iterator[NormalizedMessage]:
    encoding = _detect_text_encoding(path)
    if encoding is None:
        raise ParserError("unsupported_format", "source is not a supported text encoding")
    stack: list[ET.Element] = []
    record_index = 0
    try:
        with path.open("r", encoding=encoding, newline="") as handle:
            reader = _SecureXmlReader(handle)
            for event, element in ET.iterparse(reader, events=("start", "end")):
                if event == "start":
                    stack.append(element)
                    continue
                if _xml_is_message_element(element):
                    record_index += 1
                    record = _xml_record(element, record_index)
                    yield record
                    if len(stack) > 1:
                        stack[-2].remove(element)
                stack.pop()
    except _XmlSecurityError as exc:
        raise ParserError("unsupported_format", str(exc)) from exc
    except ET.ParseError as exc:
        raise ParserError("invalid_record", f"XML parsing failed: {exc}") from exc
    except OSError as exc:
        raise ParserError("unsupported_format", f"XML source could not be read: {exc}") from exc


def _xml_record(element: ET.Element, index: int) -> NormalizedMessage:
    values = _xml_record_values(element)
    try:
        return NormalizedMessage.from_mapping(values)
    except MessageValidationError as exc:
        raise ParserError("invalid_record", f"XML message {index}: {exc}") from exc


def _xml_record_values(element: ET.Element) -> dict[str, Any]:
    fields: dict[str, str] = {}
    for name, value in element.attrib.items():
        canonical = _xml_field_name(name)
        if canonical and isinstance(value, str) and value.strip():
            fields.setdefault(canonical, value.strip())

    for child in element.iter():
        if child is element:
            continue
        if _xml_local_name(child.tag) in _XML_ATTACHMENT_TAGS:
            continue
        canonical = _xml_field_name(child.tag)
        if not canonical:
            continue
        value = _xml_text(child)
        if canonical == "sender_id":
            sender_attr = _xml_attribute(child, "id", "sender_id", "value")
            if sender_attr:
                fields.setdefault("sender_id", sender_attr)
                if value:
                    fields.setdefault("sender_name", value)
            elif value:
                fields.setdefault(canonical, value)
        elif value:
            fields.setdefault(canonical, value)

    content = fields.get("content") or _xml_message_text(element)
    sender_id = fields.get("sender_id") or fields.get("sender_name") or ""
    sender_name = fields.get("sender_name") or sender_id
    return {
        "sender_id": sender_id,
        "sender_name": sender_name,
        "content": content,
        "timestamp": fields.get("timestamp", ""),
        "message_type": fields.get("message_type") or "text",
        "attachments": _xml_attachments(element),
    }


def _xml_attachments(element: ET.Element) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    for child in element.iter():
        if child is element or _xml_local_name(child.tag) not in _XML_ATTACHMENT_TAGS:
            continue
        attributes = {
            _xml_local_name(name): value.strip()
            for name, value in child.attrib.items()
            if isinstance(value, str) and value.strip()
        }
        reference = (
            attributes.get("path")
            or attributes.get("src")
            or attributes.get("href")
            or attributes.get("url")
            or _xml_text(child)
        )
        if not reference:
            continue
        item: dict[str, str] = {
            "path": reference,
            "kind": _xml_local_name(child.tag),
        }
        for source, target in (
            ("name", "name"),
            ("filename", "name"),
            ("mime", "media_type"),
            ("media_type", "media_type"),
            ("type", "media_type"),
            ("size", "size"),
        ):
            if attributes.get(source):
                item[target] = attributes[source]
        attachments.append(item)
    return attachments


def _xml_field_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    local = value.rsplit("}", 1)[-1].rsplit(":", 1)[-1]
    normalized = re.sub(r"[^\w]+", "_", local.casefold()).strip("_")
    for canonical, aliases in _XML_FIELD_ALIASES.items():
        if normalized in {_normalize_xml_alias(alias) for alias in aliases}:
            return canonical
    return None


def _normalize_xml_alias(value: str) -> str:
    return re.sub(r"[^\w]+", "_", value.casefold()).strip("_")


def _xml_attribute(element: ET.Element, *names: str) -> str | None:
    normalized = {
        name.rsplit("}", 1)[-1].rsplit(":", 1)[-1].casefold(): value
        for name, value in element.attrib.items()
    }
    for name in names:
        value = normalized.get(name.casefold())
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _xml_text(element: ET.Element) -> str:
    return _normalize_xml_text("".join(element.itertext()))


def _xml_message_text(element: ET.Element) -> str:
    parts: list[str] = []
    if element.text:
        parts.append(element.text)
    for child in element:
        if _xml_field_name(child.tag) or _xml_is_message_element(child):
            continue
        text = _xml_text(child)
        if text:
            parts.append(text)
    return _normalize_xml_text(" ".join(parts))


def _normalize_xml_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _xml_local_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.rsplit("}", 1)[-1].rsplit(":", 1)[-1].casefold()


def _xml_is_message_element(element: ET.Element) -> bool:
    return _xml_local_name(element.tag) in _XML_MESSAGE_TAGS


def _looks_like_xml_sample(sample: str) -> bool:
    return sample.lstrip().startswith("<")


def _looks_like_html_sample(sample: str) -> bool:
    return bool(
        re.search(
            r"<!doctype\s+html\b|<\s*(?:html|head|body|main|section|article|div|ul|ol|li)\b",
            sample,
            re.IGNORECASE,
        )
    )


def _generic_html_message_hint(sample: str) -> bool:
    return bool(
        re.search(
            r"(?:class|id)\s*=\s*[\"'][^\"']*"
            r"\b(?:message|msg|chat-message|message-item|chat-item|chat-entry|"
            r"conversation-message|record|entry|utterance|bubble)\b"
            r"[^\"']*[\"']",
            sample,
            re.IGNORECASE,
        )
        or re.search(
            r"\bdata-(?:message-id|sender-id|sender|author-id|author|speaker|"
            r"user-id|timestamp|time)\s*=",
            sample,
            re.IGNORECASE,
        )
    )


def _xml_message_hint(sample: str) -> bool:
    return bool(
        re.search(
            r"<\s*(?:[\w.-]+:)?(?:message|msg|record|item|entry|chat|utterance)(?:\s|/?>)",
            sample,
            re.IGNORECASE,
        )
    )


def _xml_security_violation(sample: str) -> bool:
    normalized = sample.casefold()
    return "<!doctype" in normalized or "<!entity" in normalized


class _XmlSecurityError(ValueError):
    pass


class _SecureXmlReader:
    def __init__(self, handle: Any) -> None:
        self._handle = handle
        self._tail = ""

    def read(self, size: int = -1) -> str:
        chunk = self._handle.read(size)
        if not chunk:
            return chunk
        combined = self._tail + chunk.casefold()
        if "<!doctype" in combined or "<!entity" in combined:
            raise _XmlSecurityError("XML DOCTYPE/ENTITY declarations are not supported")
        self._tail = combined[-16:]
        return chunk


def _requires_streaming_json(path: Path) -> bool:
    """Keep a single standard-library JSON document below the eager-load budget."""
    try:
        return path.is_file() and path.stat().st_size > MAX_EAGER_JSON_BYTES
    except OSError:
        # Let the parser's existing validation surface inaccessible sources as a
        # stable parser error instead of masking that reason during probing.
        return False


def _stream_record_byte_limit(source: ParserSource) -> int | None:
    """Read an optional training-only record limit from trusted service metadata."""
    value = source.metadata.get("max_record_bytes")
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > MAX_TRAINING_STREAM_RECORD_BYTES
    ):
        raise ParserError(
            "invalid_stream_limit",
            "training record byte limit must be a positive bounded integer",
        )
    return value


def _iter_text_lines(
    path: Path,
    encoding: str,
    *,
    max_record_bytes: int | None,
) -> Iterator[tuple[int, str]]:
    """Yield lines without allowing a no-newline source record to grow unbounded.

    ``TextIOWrapper`` normally reads an entire physical line before returning it.
    Training imports can be up to 3 GiB, so a malicious or damaged export without a
    newline would otherwise allocate that whole line before later validation runs.
    The bounded character read is conservative for multibyte text; the subsequent
    UTF-8 byte check is the authoritative limit for the emitted training record.
    """
    character_limit = None if max_record_bytes is None else max_record_bytes + 1
    with path.open("r", encoding=encoding) as handle:
        line_number = 0
        while True:
            raw_line = handle.readline() if character_limit is None else handle.readline(character_limit)
            if not raw_line:
                return
            line_number += 1
            if max_record_bytes is not None:
                if (
                    len(raw_line) >= character_limit
                    and not raw_line.endswith(("\n", "\r"))
                ):
                    raise ParserError(
                        "training_record_too_large",
                        f"training source line {line_number} exceeds the configured byte limit",
                    )
                _require_stream_record_bytes(raw_line, max_record_bytes, line_number)
            yield line_number, raw_line


def _require_stream_record_bytes(
    value: str,
    max_record_bytes: int | None,
    line_number: int,
) -> None:
    if max_record_bytes is None:
        return
    if len(value.encode("utf-8")) > max_record_bytes:
        raise ParserError(
            "training_record_too_large",
            f"training source record at line {line_number} exceeds the configured byte limit",
        )


def _append_stream_text_continuation(
    pending: dict[str, str],
    continuation: str,
    *,
    max_record_bytes: int | None,
    line_number: int,
) -> None:
    """Append one bounded continuation after checking the accumulated record size."""
    content = pending["content"]
    if max_record_bytes is not None:
        added_bytes = len("\n".encode("utf-8")) + len(continuation.encode("utf-8"))
        if len(content.encode("utf-8")) + added_bytes > max_record_bytes:
            raise ParserError(
                "training_record_too_large",
                f"training source record at line {line_number} exceeds the configured byte limit",
            )
    pending["content"] = f"{content}\n{continuation}"


def _read_text_sample(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            return _decode_text(handle.read(_PROBE_BYTES))[1]
    except OSError:
        return None


def _detect_text_encoding(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_PROBE_BYTES)
    except OSError:
        return None
    return _decode_text(raw)[0]


def _decode_text(raw: bytes) -> tuple[str | None, str | None]:
    encodings = ("utf-8-sig", "utf-16", "utf-32", "gb18030")
    for encoding in encodings:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text:
            continue
        return encoding, text
    return None, None


_CSV_HEADER_ALIASES: dict[str, frozenset[str]] = {
    "sender_id": frozenset(
        {
            "sender_id",
            "sender",
            "from",
            "speaker",
            "author",
            "发送者",
            "发送人",
            "发言人",
            "用户",
            "用户名",
        }
    ),
    "sender_name": frozenset({"sender_name", "sendername", "昵称", "姓名", "名称"}),
    "content": frozenset(
        {
            "content",
            "message",
            "text",
            "body",
            "消息",
            "消息内容",
            "内容",
            "文本",
        }
    ),
    "timestamp": frozenset(
        {
            "timestamp",
            "time",
            "date",
            "created_at",
            "created",
            "时间",
            "时间戳",
            "日期",
        }
    ),
    "message_type": frozenset({"message_type", "type", "kind", "类型", "消息类型"}),
    "attachments": frozenset(
        {
            "attachment",
            "attachments",
            "media",
            "media_ref",
            "media_refs",
            "file",
            "file_path",
            "attachment_path",
            "附件",
            "附件路径",
            "媒体",
            "文件",
            "文件路径",
            "图片",
        }
    ),
}


def _csv_header_info(sample: str) -> tuple[tuple[str, ...], csv.Dialect] | None:
    lines = [line for line in sample.splitlines() if line.strip()]
    if not lines:
        return None
    header_line = lines[0]
    try:
        dialect = csv.Sniffer().sniff(header_line, delimiters=",;\t|")
        headers = tuple(next(csv.reader(io.StringIO(header_line), dialect=dialect, strict=True)))
    except (csv.Error, StopIteration):
        return None
    normalized = tuple(header.strip() for header in headers if header.strip())
    if len(normalized) < 2:
        return None
    return normalized, dialect


def _normalize_csv_header(value: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", "_", value.strip().casefold())
    return normalized.strip("_")


def _csv_header_map(headers: Sequence[str]) -> dict[str, str]:
    normalized_headers = {
        _normalize_csv_header(header): header
        for header in headers
        if isinstance(header, str) and header.strip()
    }
    result: dict[str, str] = {}
    for canonical, aliases in _CSV_HEADER_ALIASES.items():
        for alias in aliases:
            normalized_alias = _normalize_csv_header(alias)
            if normalized_alias in normalized_headers:
                result[canonical] = normalized_headers[normalized_alias]
                break
    return result


def _csv_has_required_headers(header_map: Mapping[str, str]) -> bool:
    return bool(
        (header_map.get("sender_id") or header_map.get("sender_name"))
        and header_map.get("content")
        and header_map.get("timestamp")
    )


def _csv_record_values(
    row: Mapping[str | None, str | None],
    header_map: Mapping[str, str],
) -> dict[str, str]:
    def value_for(key: str) -> str:
        header = header_map.get(key)
        if not header:
            return ""
        value = row.get(header)
        return value.strip() if isinstance(value, str) else ""

    sender_name = value_for("sender_name")
    sender_id = value_for("sender_id") or sender_name
    return {
        "sender_id": sender_id,
        "sender_name": sender_name or sender_id,
        "content": value_for("content"),
        "timestamp": value_for("timestamp"),
        "message_type": value_for("message_type") or "text",
        "attachments": value_for("attachments"),
    }


def _looks_like_json_sample(sample: str) -> bool:
    stripped = sample.lstrip()
    if stripped.startswith("{"):
        return True
    return stripped.startswith("[") and len(stripped) > 1 and not stripped[1].isdigit()


def _complete_jsonl_sample_lines(
    sample: str,
    *,
    final_line_complete: bool = False,
) -> list[str]:
    """Return only complete JSONL rows from a bounded text probe."""
    lines = sample.splitlines()
    if sample and not final_line_complete and not sample.endswith(("\n", "\r")):
        # The final row may have been cut by the fixed-size probe. Treating that
        # partial JSON as invalid would reject otherwise streamable multi-GB files.
        lines = lines[:-1]
    return [line.strip() for line in lines if line.strip()]


def _looks_like_jsonl_messages(sample: str) -> bool:
    """Recognize complete JSONL message rows without decoding the whole source."""
    lines = _complete_jsonl_sample_lines(sample)
    if not lines:
        return False
    for line in lines[:32]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(value, Mapping) or not _looks_like_message(value):
            return False
    return True


def _probe_covers_source(path: Path) -> bool:
    """Tell whether a fixed-size probe necessarily reached the end of a file."""
    try:
        return path.is_file() and path.stat().st_size <= _PROBE_BYTES
    except OSError:
        return False


def _platform_marker(
    source: ParserSource,
    sample: str,
    *,
    source_types: frozenset[str],
    labels: tuple[str, ...],
    sample_markers: tuple[str, ...],
) -> str | None:
    requested = source.metadata.get("source_type")
    if requested in source_types:
        return "explicit"
    source_name = source.metadata.get("source_name")
    # Upload previews materialize encrypted blobs under generated names.  Once
    # a logical source name is available, it is the only stable filename signal;
    # consulting the random temp name can accidentally select a platform parser
    # when it happens to contain a marker such as ``qq``.
    source_labels = (
        [source_name]
        if isinstance(source_name, str) and source_name.strip()
        else [source.path.name]
    )
    label = " ".join(source_labels).lower()
    if any(marker in label for marker in labels):
        return "explicit"
    sample_lower = sample.lower()
    if any(marker in sample_lower for marker in sample_markers):
        return "inferred"
    return None


def _first_attr(attrs: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = attrs.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _html_attachment(tag: str, attrs: Mapping[str, str]) -> dict[str, str] | None:
    """Capture a media element's logical reference without fetching its bytes."""
    normalized_tag = tag.casefold()
    if normalized_tag in {"img", "picture", "audio", "video", "source"}:
        reference = _first_attr(attrs, "src", "data-src", "data-url", "href")
        kind = {
            "img": "image",
            "picture": "image",
            "audio": "audio",
            "video": "video",
            "source": "file",
        }[normalized_tag]
    elif normalized_tag == "a" and _first_attr(attrs, "download", "data-file", "data-attachment"):
        reference = _first_attr(attrs, "href", "data-file", "data-url")
        kind = "file"
    else:
        return None
    if not reference:
        return None
    item: dict[str, str] = {"path": reference, "kind": kind}
    name = _first_attr(attrs, "download", "data-name", "title", "alt")
    media_type = _first_attr(attrs, "type", "media-type", "data-media-type")
    size = _first_attr(attrs, "data-size", "size")
    if name:
        item["name"] = name
    if media_type:
        item["media_type"] = media_type
    if size:
        item["size"] = size
    return item


def _normalize_html_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def _join_html_text(current: str | None, value: str) -> str:
    if not current:
        return value.strip()
    return f"{current.strip()} {value.strip()}".strip()


def _text_record(values: Mapping[str, str]) -> NormalizedMessage:
    try:
        return NormalizedMessage.from_mapping(values)
    except MessageValidationError as exc:
        raise ParserError("invalid_record", str(exc)) from exc


def _load_json(path: Path) -> Any:
    encoding = _detect_text_encoding(path)
    if encoding is None:
        raise ParserError("unsupported_format", "source is not a supported text encoding")
    with path.open("r", encoding=encoding) as handle:
        return json.load(handle)


def _message_items(data: Any) -> list[Mapping[str, Any]]:
    if isinstance(data, list):
        items = data
    elif isinstance(data, Mapping) and isinstance(data.get("messages"), list):
        items = data["messages"]
    else:
        raise ParserError("unsupported_format", "JSON must be a message array or contain messages")
    if not all(isinstance(item, Mapping) for item in items):
        raise ParserError("invalid_record", "every JSON message must be an object")
    return list(items)


def _looks_like_message(value: Mapping[str, Any]) -> bool:
    sender_keys = {"sender_id", "sender"}
    content_keys = {"content", "message"}
    timestamp_keys = {"timestamp", "time"}
    return bool(sender_keys.intersection(value) and content_keys.intersection(value) and timestamp_keys.intersection(value))


def _assign_record_ids(
    records: Sequence[NormalizedMessage],
    source: ParserSource,
    source_type: str,
) -> tuple[NormalizedMessage, ...]:
    """Attach server-stable IDs before parsed records reach preview or storage."""
    namespace = _record_id_namespace(source)

    normalized: list[NormalizedMessage] = []
    for index, record in enumerate(records):
        record_id = _stable_record_id(namespace, source_type, index)
        normalized.append(record.with_record_id(record_id))
    return tuple(normalized)


def _record_id_namespace(source: ParserSource) -> str:
    namespace = source.metadata.get("record_id_namespace")
    if not isinstance(namespace, str) or not namespace.strip():
        namespace = source.metadata.get("source_name")
    if not isinstance(namespace, str) or not namespace.strip():
        namespace = source.path.name
    return namespace.strip()


def _stable_record_id(namespace: str, source_type: str, index: int) -> str:
    return hashlib.sha256(f"{namespace}:{source_type}:{index}".encode("utf-8")).hexdigest()
