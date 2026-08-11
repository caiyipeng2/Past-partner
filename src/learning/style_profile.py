"""Deterministic style profiles built from canonical chat messages."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
import re
import string
from statistics import median
from typing import Any

from src.domain.messages import NormalizedMessage


_RELATIONSHIP_TYPES = frozenset({"father", "mother", "relative", "friend", "partner", "custom"})
_TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")
_EMOJI_RANGES = ((0x1F300, 0x1FAFF), (0x2600, 0x27BF))
_PUNCTUATION = frozenset(string.punctuation + "，。！？；：、（）【】《》“”‘’…～·")
_POSITIVE_WORDS = frozenset({"开心", "高兴", "喜欢", "爱", "棒", "好", "不错", "赞", "快乐", "谢谢"})
_NEGATIVE_WORDS = frozenset({"难过", "生气", "讨厌", "烦", "差", "不好", "糟糕", "痛苦", "失望"})


class StyleProfileError(ValueError):
    """Raised when a style profile cannot be generated safely."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class StyleProfile:
    profile_version: int
    message_count: int
    message_length: dict[str, Any]
    vocabulary: dict[str, Any]
    punctuation: dict[str, Any]
    emoji: dict[str, Any]
    cadence: dict[str, Any]
    emotion_tendency: dict[str, Any]
    preferred_forms_of_address: tuple[dict[str, Any], ...]
    relationship_context: dict[str, Any]
    relationship_behavior: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_version": self.profile_version,
            "message_count": self.message_count,
            "message_length": dict(self.message_length),
            "vocabulary": {
                **self.vocabulary,
                "top_tokens": [dict(item) for item in self.vocabulary["top_tokens"]],
            },
            "punctuation": {
                **self.punctuation,
                "counts": dict(self.punctuation["counts"]),
                "top_punctuation": [dict(item) for item in self.punctuation["top_punctuation"]],
            },
            "emoji": {
                **self.emoji,
                "counts": dict(self.emoji["counts"]),
                "top_emojis": [dict(item) for item in self.emoji["top_emojis"]],
            },
            "cadence": dict(self.cadence),
            "emotion_tendency": {
                "counts": dict(self.emotion_tendency["counts"]),
                "rates": dict(self.emotion_tendency["rates"]),
            },
            "preferred_forms_of_address": [dict(item) for item in self.preferred_forms_of_address],
            "relationship_context": dict(self.relationship_context),
            "relationship_behavior": dict(self.relationship_behavior),
        }


class StyleProfileExtractor:
    """Extract bounded, provider-independent style statistics from canonical records."""

    def extract(
        self,
        records: Iterable[NormalizedMessage],
        *,
        persona_sender_ids: Collection[str],
        user_sender_ids: Collection[str] = (),
        known_addresses: Collection[str] = (),
        relationship_type: str | None = None,
        relationship_label: str | None = None,
        preferred_address: str | None = None,
    ) -> StyleProfile:
        persona_ids = _sender_ids(persona_sender_ids, "persona_sender_ids")
        user_ids = _sender_ids(user_sender_ids, "user_sender_ids", allow_empty=True)
        addresses = _metadata_list(known_addresses, "known_addresses", 80)
        normalized_preferred_address = None
        if preferred_address is not None:
            normalized_preferred_address = _metadata_text(preferred_address, "preferred_address", 80)
            addresses = tuple(dict.fromkeys((*addresses, normalized_preferred_address)))
        if relationship_type is not None and relationship_type not in _RELATIONSHIP_TYPES:
            raise StyleProfileError("invalid_relationship", "relationship_type is not supported")
        if relationship_label is not None:
            relationship_label = _metadata_text(relationship_label, "relationship_label", 40)

        source_count = 0
        user_count = 0
        lengths: list[int] = []
        token_counts: Counter[str] = Counter()
        punctuation_counts: Counter[str] = Counter()
        emoji_counts: Counter[str] = Counter()
        punctuation_message_count = 0
        emoji_message_count = 0
        address_counts: Counter[str] = Counter()
        emotion_counts: Counter[str] = Counter()
        timestamps: list[datetime] = []

        for record in records:
            if not isinstance(record, NormalizedMessage):
                raise StyleProfileError("invalid_record", "style profiles require canonical messages")
            source_count += 1
            if record.sender_id in user_ids:
                user_count += 1
            if record.sender_id not in persona_ids or not record.content.strip():
                continue

            content = record.content.strip()
            lengths.append(len(content))
            token_counts.update(_TOKEN.findall(content))
            punctuation_in_message = [char for char in content if char in _PUNCTUATION]
            if punctuation_in_message:
                punctuation_message_count += 1
                punctuation_counts.update(punctuation_in_message)
            emoji_in_message = _emojis(content)
            if emoji_in_message:
                emoji_message_count += 1
                emoji_counts.update(emoji_in_message)
            emotion_counts[_emotion(content)] += 1
            for address in addresses:
                count = content.count(address)
                if count:
                    address_counts[address] += count
            timestamp = _timestamp(record.timestamp)
            if timestamp is not None:
                timestamps.append(timestamp)

        if not lengths:
            raise StyleProfileError(
                "persona_messages_required",
                "at least one persona-authored text message is required",
            )

        message_count = len(lengths)
        sorted_lengths = sorted(lengths)
        emotion_total = sum(emotion_counts.values())
        relationship_context = {
            key: value
            for key, value in (
                ("relationship_type", relationship_type),
                ("relationship_label", relationship_label),
                ("preferred_address", normalized_preferred_address),
            )
            if value is not None
        }
        return StyleProfile(
            profile_version=1,
            message_count=message_count,
            message_length={
                "mean": sum(lengths) / message_count,
                "median": float(median(sorted_lengths)),
                "min": sorted_lengths[0],
                "max": sorted_lengths[-1],
            },
            vocabulary={
                "token_count": sum(token_counts.values()),
                "unique_token_count": len(token_counts),
                "top_tokens": _top_counts(token_counts),
            },
            punctuation={
                "message_usage_rate": punctuation_message_count / message_count,
                "average_per_message": sum(punctuation_counts.values()) / message_count,
                "counts": dict(punctuation_counts),
                "top_punctuation": _top_counts(punctuation_counts),
            },
            emoji={
                "message_count": emoji_message_count,
                "usage_rate": emoji_message_count / message_count,
                "average_per_message": sum(emoji_counts.values()) / message_count,
                "counts": dict(emoji_counts),
                "top_emojis": _top_counts(emoji_counts),
            },
            cadence=_cadence(timestamps),
            emotion_tendency={
                "counts": {name: emotion_counts.get(name, 0) for name in ("positive", "negative", "neutral")},
                "rates": {
                    name: emotion_counts.get(name, 0) / emotion_total if emotion_total else 0.0
                    for name in ("positive", "negative", "neutral")
                },
            },
            preferred_forms_of_address=tuple(_top_counts(address_counts)),
            relationship_context=relationship_context,
            relationship_behavior={
                "source_message_count": source_count,
                "persona_message_count": message_count,
                "user_message_count": user_count,
                "persona_share": message_count / source_count if source_count else 0.0,
            },
        )


def _sender_ids(value: Iterable[str], field_name: str, *, allow_empty: bool = False) -> frozenset[str]:
    if isinstance(value, (str, bytes)):
        raise StyleProfileError("invalid_sender_ids", f"{field_name} must be a collection of IDs")
    try:
        normalized = frozenset(_metadata_text(item, field_name, 256) for item in value)
    except TypeError as exc:
        raise StyleProfileError("invalid_sender_ids", f"{field_name} must be a collection of IDs") from exc
    if not normalized and not allow_empty:
        raise StyleProfileError("persona_senders_required", f"{field_name} cannot be empty")
    return normalized


def _metadata_list(value: Iterable[str], field_name: str, maximum: int) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise StyleProfileError("invalid_metadata", f"{field_name} must be a list")
    try:
        return tuple(dict.fromkeys(_metadata_text(item, field_name, maximum) for item in value))
    except TypeError as exc:
        raise StyleProfileError("invalid_metadata", f"{field_name} must be a list") from exc


def _metadata_text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise StyleProfileError("invalid_metadata", f"{field_name} must be a bounded string")
    return value.strip()


def _top_counts(counter: Counter[str], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _emojis(value: str) -> list[str]:
    return [
        char
        for char in value
        if any(start <= ord(char) <= end for start, end in _EMOJI_RANGES)
    ]


def _emotion(value: str) -> str:
    positive = sum(value.count(word) for word in _POSITIVE_WORDS)
    negative = sum(value.count(word) for word in _NEGATIVE_WORDS)
    if positive > negative and positive:
        return "positive"
    if negative > positive and negative:
        return "negative"
    return "neutral"


def _timestamp(value: str) -> datetime | None:
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
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _cadence(timestamps: list[datetime]) -> dict[str, Any]:
    ordered = sorted(timestamps)
    intervals = [
        (right - left).total_seconds()
        for left, right in zip(ordered, ordered[1:])
        if right >= left
    ]
    return {
        "timestamp_count": len(ordered),
        "interval_count": len(intervals),
        "average_interval_seconds": sum(intervals) / len(intervals) if intervals else None,
        "median_interval_seconds": float(median(intervals)) if intervals else None,
        "active_span_seconds": (ordered[-1] - ordered[0]).total_seconds() if len(ordered) > 1 else 0.0,
    }
