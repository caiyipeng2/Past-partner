"""Evidence-bound long-term memory candidates from canonical chat records."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import re
from typing import Any

from src.domain.messages import NormalizedMessage


_RELATIONSHIP_TYPES = frozenset({"father", "mother", "relative", "friend", "partner", "custom"})
_REVIEW_STATES = frozenset({"needs_review", "accepted", "rejected"})
_MAX_EVIDENCE_TEXT = 240
_RECORD_ID = re.compile(r"^[a-fA-F0-9]{64}$")
_WHITESPACE = re.compile(r"\s+")
_RELATIONSHIP = re.compile(r"(?:我们是|在一起|情侣|恋人|朋友|家人|亲人)")
_PREFERENCE = re.compile(r"(?:喜欢|爱吃|爱喝|不喜欢|讨厌|最爱|偏好|习惯)")
_EVENT = re.compile(r"(?:第一次|一起|旅行|约会|见面|看电影|纪念日|生日|分手|认识|去过|去了|周末)")
_FACT = re.compile(r"(?:叫|住在|来自|工作在|工作于|生日是|年龄是|是\S{1,20})")


class LongTermMemoryError(ValueError):
    """Raised when memory candidates cannot be generated or reviewed safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    memory_id: str
    kind: str
    text: str
    source_record_ids: tuple[str, ...]
    occurred_at: str | None
    confidence: float
    review_state: str
    speaker_scope: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "kind": self.kind,
            "text": self.text,
            "source_record_ids": list(self.source_record_ids),
            "occurred_at": self.occurred_at,
            "confidence": self.confidence,
            "review_state": self.review_state,
            "speaker_scope": self.speaker_scope,
        }


@dataclass(frozen=True, slots=True)
class LongTermMemory:
    memory_version: int
    source_record_count: int
    accepted_record_count: int
    candidates: tuple[MemoryCandidate, ...]
    relationship_context: dict[str, Any]

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_version": self.memory_version,
            "source_record_count": self.source_record_count,
            "accepted_record_count": self.accepted_record_count,
            "candidate_count": self.candidate_count,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "relationship_context": dict(self.relationship_context),
        }

    def review(self, memory_id: str, review_state: str) -> "LongTermMemory":
        if review_state not in _REVIEW_STATES:
            raise LongTermMemoryError("invalid_review_state", "review_state is not supported")
        updated = []
        found = False
        for candidate in self.candidates:
            if candidate.memory_id == memory_id:
                updated.append(replace(candidate, review_state=review_state))
                found = True
            else:
                updated.append(candidate)
        if not found:
            raise LongTermMemoryError("memory_not_found", "memory_id does not exist")
        return replace(self, candidates=tuple(updated))


class LongTermMemoryExtractor:
    """Generate auditable memory candidates without model calls or source persistence."""

    def extract(
        self,
        records: Iterable[NormalizedMessage],
        *,
        accepted_record_ids: Iterable[str] | None = None,
        persona_sender_ids: Iterable[str] = (),
        user_sender_ids: Iterable[str] = (),
        relationship_type: str | None = None,
        relationship_label: str | None = None,
        max_candidates: int = 500,
    ) -> LongTermMemory:
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 0 < max_candidates <= 1000:
            raise LongTermMemoryError("invalid_candidate_limit", "max_candidates must be between 1 and 1000")
        persona_ids = _sender_ids(persona_sender_ids, "persona_sender_ids")
        user_ids = _sender_ids(user_sender_ids, "user_sender_ids", allow_empty=True)
        accepted_ids = None if accepted_record_ids is None else _record_ids(accepted_record_ids)
        if relationship_type is not None and relationship_type not in _RELATIONSHIP_TYPES:
            raise LongTermMemoryError("invalid_relationship", "relationship_type is not supported")
        if relationship_label is not None:
            relationship_label = _metadata_text(relationship_label, "relationship_label", 40)

        normalized_records: list[NormalizedMessage] = []
        seen_ids: set[str] = set()
        for record in records:
            if not isinstance(record, NormalizedMessage):
                raise LongTermMemoryError("invalid_record", "long-term memory requires canonical messages")
            if record.record_id is None:
                raise LongTermMemoryError("record_id_required", "canonical messages need record_id evidence")
            if record.record_id in seen_ids:
                raise LongTermMemoryError("duplicate_record_id", "record_id must be unique within a memory extraction")
            seen_ids.add(record.record_id)
            normalized_records.append(record)

        if accepted_ids is not None:
            unknown = accepted_ids.difference(seen_ids)
            if unknown:
                raise LongTermMemoryError("unknown_accepted_record", "accepted_record_ids contain unknown evidence")
        selected = [record for record in normalized_records if accepted_ids is None or record.record_id in accepted_ids]
        if not selected:
            raise LongTermMemoryError("accepted_messages_required", "at least one accepted canonical message is required")

        candidates: list[MemoryCandidate] = []
        candidate_indexes: dict[tuple[str, str], int] = {}
        for record in selected:
            text = _bounded_text(record.content)
            if not text:
                continue
            kind = _classify(text)
            if kind is None:
                continue
            occurred_at = _canonical_timestamp(record.timestamp)
            speaker_scope = _speaker_scope(record.sender_id, persona_ids, user_ids)
            _add_candidate(
                candidates,
                candidate_indexes,
                kind=kind,
                text=text,
                record=record,
                occurred_at=occurred_at,
                speaker_scope=speaker_scope,
                confidence=_confidence(kind),
                max_candidates=max_candidates,
            )
            if kind == "event":
                timeline_text = _bounded_text(f"{occurred_at}: {text}" if occurred_at else text)
                _add_candidate(
                    candidates,
                    candidate_indexes,
                    kind="timeline",
                    text=timeline_text,
                    record=record,
                    occurred_at=occurred_at,
                    speaker_scope=speaker_scope,
                    confidence=0.7,
                    max_candidates=max_candidates,
                )

        if not candidates:
            raise LongTermMemoryError("no_memory_candidates", "accepted messages contain no extractable memory evidence")
        relationship_context = {
            key: value
            for key, value in (
                ("relationship_type", relationship_type),
                ("relationship_label", relationship_label),
            )
            if value is not None
        }
        return LongTermMemory(
            memory_version=1,
            source_record_count=len(normalized_records),
            accepted_record_count=len(selected),
            candidates=tuple(candidates),
            relationship_context=relationship_context,
        )


def _add_candidate(
    candidates: list[MemoryCandidate],
    indexes: dict[tuple[str, str], int],
    *,
    kind: str,
    text: str,
    record: NormalizedMessage,
    occurred_at: str | None,
    speaker_scope: str,
    confidence: float,
    max_candidates: int,
) -> None:
    key = (kind, text)
    existing_index = indexes.get(key)
    if existing_index is not None:
        current = candidates[existing_index]
        source_ids = tuple(dict.fromkeys((*current.source_record_ids, record.record_id or "")))
        candidates[existing_index] = replace(
            current,
            source_record_ids=source_ids,
            occurred_at=current.occurred_at or occurred_at,
        )
        return
    if len(candidates) >= max_candidates:
        raise LongTermMemoryError("candidate_limit_exceeded", "memory candidate limit exceeded")
    memory_id = hashlib.sha256(f"{kind}:{text}".encode("utf-8")).hexdigest()
    indexes[key] = len(candidates)
    candidates.append(
        MemoryCandidate(
            memory_id=memory_id,
            kind=kind,
            text=text,
            source_record_ids=(record.record_id or "",),
            occurred_at=occurred_at,
            confidence=confidence,
            review_state="needs_review",
            speaker_scope=speaker_scope,
        )
    )


def _classify(text: str) -> str | None:
    if _RELATIONSHIP.search(text):
        return "relationship"
    if _PREFERENCE.search(text):
        return "preference"
    if _EVENT.search(text):
        return "event"
    if _FACT.search(text):
        return "fact"
    return None


def _confidence(kind: str) -> float:
    return {
        "relationship": 0.78,
        "preference": 0.8,
        "event": 0.75,
        "fact": 0.65,
    }.get(kind, 0.7)


def _bounded_text(value: str) -> str:
    normalized = _WHITESPACE.sub(" ", value).strip()
    if len(normalized) <= _MAX_EVIDENCE_TEXT:
        return normalized
    return f"{normalized[: _MAX_EVIDENCE_TEXT - 1].rstrip()}…"


def _canonical_timestamp(value: str) -> str | None:
    text = value.strip()
    if text.startswith("line:"):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC).isoformat()


def _speaker_scope(sender_id: str, persona_ids: frozenset[str], user_ids: frozenset[str]) -> str:
    if sender_id in persona_ids:
        return "persona"
    if sender_id in user_ids:
        return "user"
    return "unknown"


def _sender_ids(value: Iterable[str], field_name: str, *, allow_empty: bool = False) -> frozenset[str]:
    if isinstance(value, (str, bytes)):
        raise LongTermMemoryError("invalid_sender_ids", f"{field_name} must be a collection of IDs")
    try:
        normalized = frozenset(_metadata_text(item, field_name, 256) for item in value)
    except TypeError as exc:
        raise LongTermMemoryError("invalid_sender_ids", f"{field_name} must be a collection of IDs") from exc
    if not normalized and not allow_empty:
        raise LongTermMemoryError("persona_senders_required", f"{field_name} cannot be empty")
    return normalized


def _record_ids(value: Iterable[str]) -> frozenset[str]:
    if isinstance(value, (str, bytes)):
        raise LongTermMemoryError("invalid_record_ids", "accepted_record_ids must be a collection of IDs")
    try:
        normalized = frozenset(_record_id(item) for item in value)
    except TypeError as exc:
        raise LongTermMemoryError("invalid_record_ids", "accepted_record_ids must be a collection of IDs") from exc
    if not normalized:
        raise LongTermMemoryError("accepted_messages_required", "accepted_record_ids cannot be empty")
    return normalized


def _record_id(value: object) -> str:
    if not isinstance(value, str) or not _RECORD_ID.fullmatch(value):
        raise LongTermMemoryError("invalid_record_ids", "accepted_record_ids must contain 64-character hexadecimal IDs")
    return value.lower()


def _metadata_text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise LongTermMemoryError("invalid_metadata", f"{field_name} must be a bounded string")
    return value.strip()
