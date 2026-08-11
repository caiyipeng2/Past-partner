"""Provider-independent retrieval for reviewed long-term memory candidates.

The first retrieval implementation deliberately uses a deterministic sparse
vector (token overlap) instead of an embedding provider.  The public result
contract keeps ranking, budget, and provenance separate so a real embedding
index can replace the scorer without changing privacy behavior or callers.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable

from src.learning.long_term_memory import LongTermMemory


_TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_QUERY_LENGTH = 400
_MAX_CANDIDATES = 100
_MAX_TOKENS = 32_000
_MAX_AGE_DAYS = 36_500
_REVIEWED_STATE = "accepted"
_SCOPES = frozenset({"persona", "user", "unknown"})


class VectorRetrievalError(ValueError):
    """Raised when a retrieval request cannot be evaluated safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RetrievalBudget:
    max_candidates: int
    max_tokens: int
    max_age_days: int | None
    allowed_speaker_scopes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_candidates": self.max_candidates,
            "max_tokens": self.max_tokens,
            "max_age_days": self.max_age_days,
            "allowed_speaker_scopes": list(self.allowed_speaker_scopes),
        }


@dataclass(frozen=True, slots=True)
class RetrievedMemory:
    """A bounded evidence excerpt selected for one conversation turn."""

    memory_id: str
    kind: str
    text: str
    source_record_ids: tuple[str, ...]
    occurred_at: str | None
    confidence: float
    review_state: str
    speaker_scope: str
    score: float
    lexical_score: float
    recency_score: float
    token_count: int

    @property
    def source_excerpt(self) -> str:
        """Expose the candidate text under the design's evidence terminology."""

        return self.text

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "kind": self.kind,
            "text": self.text,
            "source_excerpt": self.source_excerpt,
            "source_record_ids": list(self.source_record_ids),
            "occurred_at": self.occurred_at,
            "confidence": self.confidence,
            "review_state": self.review_state,
            "speaker_scope": self.speaker_scope,
            "score": self.score,
            "lexical_score": self.lexical_score,
            "recency_score": self.recency_score,
            "token_count": self.token_count,
        }


@dataclass(frozen=True, slots=True)
class MemoryRetrievalResult:
    """Auditable retrieval output without retaining the raw query text."""

    retrieval_version: int
    memory_version: int
    query_fingerprint: str
    query_token_count: int
    memories: tuple[RetrievedMemory, ...]
    examined_count: int
    used_tokens: int
    excluded_counts: dict[str, int]
    budget: RetrievalBudget

    def to_dict(self) -> dict[str, Any]:
        return {
            "retrieval_version": self.retrieval_version,
            "memory_version": self.memory_version,
            "query_fingerprint": self.query_fingerprint,
            "query_token_count": self.query_token_count,
            "memories": [memory.to_dict() for memory in self.memories],
            "examined_count": self.examined_count,
            "used_tokens": self.used_tokens,
            "excluded_counts": dict(self.excluded_counts),
            "budget": self.budget.to_dict(),
        }


class VectorMemoryRetriever:
    """Select reviewed evidence with deterministic privacy and context budgets."""

    def retrieve(
        self,
        memory: LongTermMemory,
        query: str,
        *,
        as_of: str | datetime | None = None,
        max_candidates: int = 5,
        max_tokens: int = 800,
        max_age_days: int | None = None,
        allowed_speaker_scopes: Iterable[str] = ("persona", "user"),
    ) -> MemoryRetrievalResult:
        if not isinstance(memory, LongTermMemory):
            raise VectorRetrievalError("invalid_memory", "memory must be a LongTermMemory instance")
        normalized_query = _query_text(query)
        query_tokens = _tokens(normalized_query)
        if not query_tokens:
            raise VectorRetrievalError("query_required", "query must contain searchable text")
        max_candidates = _bounded_int(max_candidates, 1, _MAX_CANDIDATES, "candidate")
        max_tokens = _bounded_int(max_tokens, 1, _MAX_TOKENS, "token")
        max_age_days = _age_budget(max_age_days)
        scopes = _scopes(allowed_speaker_scopes)
        reference_time = _reference_time(as_of, required=max_age_days is not None)
        budget = RetrievalBudget(max_candidates, max_tokens, max_age_days, scopes)

        excluded = {
            "not_accepted": 0,
            "speaker_scope": 0,
            "outside_recency_budget": 0,
            "invalid_timestamp": 0,
            "no_query_overlap": 0,
            "token_budget": 0,
        }
        ranked: list[RetrievedMemory] = []
        for candidate in memory.candidates:
            if candidate.review_state != _REVIEWED_STATE:
                excluded["not_accepted"] += 1
                continue
            if candidate.speaker_scope not in scopes:
                excluded["speaker_scope"] += 1
                continue
            candidate_tokens = _tokens(candidate.text)
            if not candidate_tokens:
                excluded["no_query_overlap"] += 1
                continue
            occurred = _timestamp(candidate.occurred_at)
            recency_score = _recency_score(occurred, reference_time)
            if max_age_days is not None:
                if occurred is None:
                    excluded["invalid_timestamp"] += 1
                    continue
                age_days = max(0.0, ((reference_time or datetime.now(UTC)) - occurred).total_seconds() / 86_400)
                if age_days > max_age_days:
                    excluded["outside_recency_budget"] += 1
                    continue
            lexical_score = _lexical_score(query_tokens, candidate_tokens, normalized_query, candidate.text)
            if lexical_score <= 0:
                excluded["no_query_overlap"] += 1
                continue
            ranked.append(
                RetrievedMemory(
                    memory_id=candidate.memory_id,
                    kind=candidate.kind,
                    text=candidate.text,
                    source_record_ids=candidate.source_record_ids,
                    occurred_at=candidate.occurred_at,
                    confidence=candidate.confidence,
                    review_state=candidate.review_state,
                    speaker_scope=candidate.speaker_scope,
                    score=round((lexical_score * 0.8) + (recency_score * 0.2), 6),
                    lexical_score=round(lexical_score, 6),
                    recency_score=round(recency_score, 6),
                    token_count=len(candidate_tokens),
                )
            )

        ranked.sort(key=lambda item: (-item.score, -item.confidence, -item.recency_score, item.memory_id))
        selected: list[RetrievedMemory] = []
        used_tokens = 0
        for item in ranked:
            if len(selected) >= max_candidates:
                break
            if used_tokens + item.token_count > max_tokens:
                excluded["token_budget"] += 1
                continue
            selected.append(item)
            used_tokens += item.token_count

        fingerprint = hashlib.sha256(normalized_query.casefold().encode("utf-8")).hexdigest()
        return MemoryRetrievalResult(
            retrieval_version=1,
            memory_version=memory.memory_version,
            query_fingerprint=fingerprint,
            query_token_count=len(query_tokens),
            memories=tuple(selected),
            examined_count=len(memory.candidates),
            used_tokens=used_tokens,
            excluded_counts=excluded,
            budget=budget,
        )


def _query_text(value: object) -> str:
    if not isinstance(value, str):
        raise VectorRetrievalError("query_required", "query must be text")
    normalized = " ".join(value.split()).strip()
    if not normalized:
        raise VectorRetrievalError("query_required", "query must contain searchable text")
    if len(normalized) > _MAX_QUERY_LENGTH or _CONTROL.search(normalized):
        raise VectorRetrievalError("invalid_query", "query exceeds the bounded retrieval input policy")
    return normalized


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(value.casefold()))


def _lexical_score(query_tokens: tuple[str, ...], candidate_tokens: tuple[str, ...], query: str, text: str) -> float:
    query_counts = Counter(query_tokens)
    candidate_counts = Counter(candidate_tokens)
    shared = sum(min(query_counts[token], candidate_counts[token]) for token in query_counts)
    denominator = max(1, sum(query_counts.values()))
    score = shared / denominator
    if query.casefold() in text.casefold():
        score += 0.15
    return min(1.0, score)


def _reference_time(value: str | datetime | None, *, required: bool) -> datetime | None:
    if value is None:
        return datetime.now(UTC) if required else None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = _timestamp(value)
        if parsed is not None:
            return parsed
    raise VectorRetrievalError("invalid_as_of", "as_of must be an ISO timestamp")


def _timestamp(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _recency_score(occurred: datetime | None, reference: datetime | None) -> float:
    if reference is None:
        return 1.0
    if occurred is None:
        return 0.5
    age_days = max(0.0, (reference - occurred).total_seconds() / 86_400)
    return 1.0 / (1.0 + age_days)


def _bounded_int(value: object, minimum: int, maximum: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise VectorRetrievalError(f"invalid_{name}_budget", f"max_{name}s is outside the supported range")
    return value


def _age_budget(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_AGE_DAYS:
        raise VectorRetrievalError("invalid_recency_budget", "max_age_days is outside the supported range")
    return value


def _scopes(value: Iterable[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise VectorRetrievalError("invalid_speaker_scope", "allowed_speaker_scopes must be a collection")
    try:
        normalized = tuple(sorted({scope.strip() for scope in value if isinstance(scope, str) and scope.strip()}))
    except TypeError as exc:
        raise VectorRetrievalError("invalid_speaker_scope", "allowed_speaker_scopes must be a collection") from exc
    if not normalized or any(scope not in _SCOPES for scope in normalized):
        raise VectorRetrievalError("invalid_speaker_scope", "allowed_speaker_scopes contains an unsupported value")
    return normalized


__all__ = [
    "MemoryRetrievalResult",
    "RetrievalBudget",
    "RetrievedMemory",
    "VectorMemoryRetriever",
    "VectorRetrievalError",
]
