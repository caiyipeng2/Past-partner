"""Canonical message schema for heterogeneous chat exports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class MessageValidationError(ValueError):
    """Raised when an imported message cannot be normalized safely."""


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    sender_id: str
    sender_name: str
    content: str
    timestamp: str
    message_type: str
    attachments: tuple[dict[str, Any], ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NormalizedMessage":
        if not isinstance(value, Mapping):
            raise MessageValidationError("message must be an object")

        sender_id = _first_text(value, "sender_id", "sender")
        sender_name = _optional_text(value.get("sender_name")) or sender_id
        content = _optional_text(value.get("content"))
        if content is None:
            content = _optional_text(value.get("message")) or ""
        timestamp = _first_text(value, "timestamp", "time")
        message_type = _optional_text(value.get("message_type"))
        if message_type is None:
            message_type = _optional_text(value.get("type")) or "text"

        raw_attachments = value.get("attachments", ())
        if raw_attachments is None:
            raw_attachments = ()
        if not isinstance(raw_attachments, (list, tuple)):
            raise MessageValidationError("attachments must be a list")

        attachments: list[dict[str, Any]] = []
        for attachment in raw_attachments:
            if not isinstance(attachment, Mapping):
                raise MessageValidationError("each attachment must be an object")
            attachments.append(dict(attachment))

        if not content and not attachments:
            raise MessageValidationError("message content or attachments are required")

        return cls(
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            timestamp=timestamp,
            message_type=message_type,
            attachments=tuple(attachments),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "timestamp": self.timestamp,
            "message_type": self.message_type,
            "attachments": [dict(item) for item in self.attachments],
        }


def _first_text(value: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        text = _optional_text(value.get(key))
        if text is not None:
            return text
    raise MessageValidationError(f"one of {', '.join(keys)} is required")


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
