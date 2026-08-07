"""Content-probed parser contracts for chat import sources."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
from itertools import islice
from pathlib import Path
import re
from typing import Any, Iterator, Mapping, Protocol, Sequence

from src.domain.messages import MessageValidationError, NormalizedMessage
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
                WeChatHtmlParser(),
                WeChatTextParser(),
                QqHtmlParser(),
                QqTextParser(),
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

    @staticmethod
    def _source(path: str | Path, metadata: Mapping[str, Any] | None) -> ParserSource:
        resolved = Path(path)
        normalized_metadata = dict(metadata or {})
        if not resolved.exists():
            raise ParserError("source_not_found", "parser source does not exist")
        if (
            resolved.is_file()
            and resolved.suffix.casefold() == ".db"
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
            if not is_named_database:
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

        pending: dict[str, str] | None = None
        with source.path.open("r", encoding=encoding) as handle:
            for line_number, raw_line in enumerate(handle, 1):
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
                    pending = {
                        "sender_id": match.group("sender").strip().lstrip("-| ").strip(),
                        "sender_name": match.group("sender").strip().lstrip("-| ").strip(),
                        "content": match.group("content").strip(),
                        "timestamp": timestamp.strip(),
                        "message_type": "text",
                    }
                elif sender_match:
                    if pending is not None:
                        yield _text_record(pending)
                    values = {
                        "sender_id": sender_match.group("sender").strip(),
                        "sender_name": sender_match.group("sender").strip(),
                        "content": sender_match.group("content").strip(),
                        "timestamp": f"line:{line_number}",
                        "message_type": "text",
                    }
                    pending = values
                elif pending is not None:
                    continuation = stripped
                    if continuation:
                        pending["content"] += f"\n{continuation}"
                else:
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

        pending: dict[str, str] | None = None
        with source.path.open("r", encoding=encoding) as handle:
            for line_number, raw_line in enumerate(handle, 1):
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
                    pending = {
                        "sender_id": sender,
                        "sender_name": sender,
                        "content": content.strip(),
                        "timestamp": timestamp.strip(),
                        "message_type": "text",
                    }
                elif sender_match:
                    if pending is not None:
                        yield _text_record(pending)
                    sender = sender_match.group("sender").strip()
                    pending = {
                        "sender_id": sender,
                        "sender_name": sender,
                        "content": sender_match.group("content").strip(),
                        "timestamp": f"line:{line_number}",
                        "message_type": "text",
                    }
                elif pending is not None:
                    pending["content"] += f"\n{stripped}"
                elif not self._is_platform_header(stripped):
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

    def __post_init__(self) -> None:
        if self.content_parts is None:
            self.content_parts = []
        if self.active_fields is None:
            self.active_fields = []


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
            context.sender_id = _first_attr(normalized, "data-sender-id", "data-sender", "data-from")
            context.sender_name = _first_attr(
                normalized,
                "data-sender-name",
                "data-nickname",
                "data-name",
            )
            context.timestamp = _first_attr(
                normalized,
                "data-timestamp",
                "data-time",
                "datetime",
            )
            context.message_type = _first_attr(normalized, "data-message-type", "data-type") or "text"
            self.contexts.append(context)
            return
        if self.contexts:
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
        if not content:
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


class GenericJsonParser(_BaseParser):
    source_type = "generic_json"

    def probe(self, source: ParserSource) -> ParserProbe:
        try:
            data = _load_json(source.path)
            _message_items(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ParserError) as exc:
            code = exc.code if isinstance(exc, ParserError) else "unsupported_format"
            reason = str(exc) or "source is not a supported JSON conversation"
            return ParserProbe(self.source_type, 0.0, False, f"{code}: {reason}")
        return ParserProbe(self.source_type, 0.98, True, "JSON conversation structure recognized")

    def validate(self, source: ParserSource) -> ParserValidation:
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
        lines = [line.strip() for line in sample.splitlines() if line.strip()]
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
        with source.path.open("r", encoding=encoding) as handle:
            for line_number, raw_line in enumerate(handle, 1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                    if not isinstance(value, Mapping):
                        raise TypeError("JSONL record must be an object")
                    yield NormalizedMessage.from_mapping(value)
                except (json.JSONDecodeError, MessageValidationError, TypeError) as exc:
                    raise ParserError("invalid_record", f"JSONL line {line_number}: {exc}") from exc


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


def _looks_like_json_sample(sample: str) -> bool:
    stripped = sample.lstrip()
    if stripped.startswith("{"):
        return True
    return stripped.startswith("[") and len(stripped) > 1 and not stripped[1].isdigit()


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
    source_labels = [source.path.name]
    if isinstance(source_name, str):
        source_labels.append(source_name)
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
    namespace = source.metadata.get("record_id_namespace")
    if not isinstance(namespace, str) or not namespace.strip():
        namespace = source.metadata.get("source_name")
    if not isinstance(namespace, str) or not namespace.strip():
        namespace = source.path.name
    namespace = namespace.strip()

    normalized: list[NormalizedMessage] = []
    for index, record in enumerate(records):
        record_id = _stable_record_id(namespace, source_type, index)
        normalized.append(record.with_record_id(record_id))
    return tuple(normalized)


def _stable_record_id(namespace: str, source_type: str, index: int) -> str:
    return hashlib.sha256(f"{namespace}:{source_type}:{index}".encode("utf-8")).hexdigest()
