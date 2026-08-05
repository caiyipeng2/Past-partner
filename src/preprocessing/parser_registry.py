"""Content-probed parser contracts for chat import sources."""

from __future__ import annotations

from dataclasses import dataclass
import json
from itertools import islice
from pathlib import Path
import re
from typing import Any, Iterator, Mapping, Protocol, Sequence

from src.domain.messages import MessageValidationError, NormalizedMessage


_TEXT_LINE = re.compile(r"^\[(?P<timestamp>[^\]]+)\]\s*(?P<sender>[^:]+):\s*(?P<content>.*)$")
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
        return cls((GenericJsonParser(), GenericJsonLinesParser(), GenericTextParser()))

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
        if not resolved.is_file():
            raise ParserError("source_not_found", "parser source file does not exist")
        return ParserSource(resolved, dict(metadata or {}))


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
        matched = sum(1 for line in non_empty if _TEXT_LINE.match(line.strip()))
        confidence = 0.92 if matched else 0.55
        return ParserProbe(self.source_type, confidence, True, "UTF-8 text source")

    def validate(self, source: ParserSource) -> ParserValidation:
        probe = self.probe(source)
        if not probe.supported:
            return ParserValidation(False, "unsupported_format", probe.reason)
        return ParserValidation(True)

    def stream_records(self, source: ParserSource) -> Iterator[NormalizedMessage]:
        with source.path.open("r", encoding="utf-8-sig") as handle:
            for line_number, raw_line in enumerate(handle, 1):
                line = raw_line.strip()
                if not line:
                    continue
                match = _TEXT_LINE.match(line)
                if match:
                    values = {
                        "sender_id": match.group("sender").strip(),
                        "sender_name": match.group("sender").strip(),
                        "content": match.group("content").strip(),
                        "timestamp": match.group("timestamp").strip(),
                        "message_type": "text",
                    }
                else:
                    values = {
                        "sender_id": "unknown",
                        "sender_name": "unknown",
                        "content": line,
                        "timestamp": f"line:{line_number}",
                        "message_type": "text",
                    }
                try:
                    yield NormalizedMessage.from_mapping(values)
                except MessageValidationError as exc:
                    raise ParserError("invalid_record", f"line {line_number}: {exc}") from exc


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
        with source.path.open("r", encoding="utf-8-sig") as handle:
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
            return handle.read(_PROBE_BYTES).decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
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
