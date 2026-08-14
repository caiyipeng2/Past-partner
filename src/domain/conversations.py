"""Validated owner/persona-scoped conversation records."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4


MAX_MESSAGE_CHARACTERS = 20_000
MAX_MESSAGES = 256
MAX_IDENTIFIER_CHARACTERS = 256


class ConversationValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_CHARACTERS:
        raise ConversationValidationError("invalid_identifier", f"{field} is invalid")
    return value


def _timestamp(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ConversationValidationError("invalid_timestamp", f"{field} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    id: str
    role: str
    content: str
    created_at: str

    @classmethod
    def create(
        cls,
        *,
        role: str,
        content: str,
        message_id: str | None = None,
        created_at: str | None = None,
    ) -> "ConversationMessage":
        if role not in {"user", "assistant"}:
            raise ConversationValidationError("invalid_message_role", "message role is invalid")
        if not isinstance(content, str) or not content.strip():
            raise ConversationValidationError("empty_message", "message content must not be empty")
        if len(content) > MAX_MESSAGE_CHARACTERS:
            raise ConversationValidationError("message_too_large", "message content is too large")
        return cls(
            id=_identifier(message_id or str(uuid4()), "message_id"),
            role=role,
            content=content,
            created_at=_timestamp(created_at or datetime.now(UTC).isoformat(), "created_at"),
        )

    @classmethod
    def from_dict(cls, value: object) -> "ConversationMessage":
        if not isinstance(value, Mapping):
            raise ConversationValidationError("invalid_message", "message must be an object")
        try:
            return cls.create(
                message_id=value["id"],
                role=value["role"],
                content=value["content"],
                created_at=value["created_at"],
            )
        except KeyError as exc:
            raise ConversationValidationError("invalid_message", "message is incomplete") from exc

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    persona_id: str
    provider_id: str
    model_id: str
    created_at: str
    updated_at: str
    messages: tuple[ConversationMessage, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        _identifier(self.id, "conversation_id")
        _identifier(self.persona_id, "persona_id")
        _identifier(self.provider_id, "provider_id")
        _identifier(self.model_id, "model_id")
        _timestamp(self.created_at, "created_at")
        _timestamp(self.updated_at, "updated_at")
        if self.schema_version != 1:
            raise ConversationValidationError("unsupported_schema_version", "conversation schema is unsupported")
        if not isinstance(self.messages, tuple) or len(self.messages) > MAX_MESSAGES:
            raise ConversationValidationError("conversation_too_large", "conversation has too many messages")
        if any(not isinstance(message, ConversationMessage) for message in self.messages):
            raise ConversationValidationError("invalid_messages", "conversation messages are invalid")

    @classmethod
    def create(cls, *, persona_id: str, provider_id: str, model_id: str) -> "Conversation":
        timestamp = datetime.now(UTC).isoformat()
        return cls(
            id=str(uuid4()),
            persona_id=_identifier(persona_id, "persona_id"),
            provider_id=_identifier(provider_id, "provider_id"),
            model_id=_identifier(model_id, "model_id"),
            created_at=timestamp,
            updated_at=timestamp,
        )

    @classmethod
    def from_dict(cls, value: object) -> "Conversation":
        if not isinstance(value, Mapping):
            raise ConversationValidationError("invalid_conversation", "conversation must be an object")
        try:
            messages = tuple(ConversationMessage.from_dict(item) for item in value.get("messages", ()))
            return cls(
                id=value["id"],
                persona_id=value["persona_id"],
                provider_id=value["provider_id"],
                model_id=value["model_id"],
                created_at=value["created_at"],
                updated_at=value["updated_at"],
                messages=messages,
                schema_version=value.get("schema_version", 1),
            )
        except KeyError as exc:
            raise ConversationValidationError("invalid_conversation", "conversation is incomplete") from exc

    def add_user_and_assistant(self, user_content: str, assistant_content: str) -> "Conversation":
        additions = (
            ConversationMessage.create(role="user", content=user_content),
            ConversationMessage.create(role="assistant", content=assistant_content),
        )
        messages = self.messages + additions
        if len(messages) > MAX_MESSAGES:
            raise ConversationValidationError("conversation_too_large", "conversation has too many messages")
        return replace(self, messages=messages, updated_at=datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "persona_id": self.persona_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": [message.to_dict() for message in self.messages],
            "schema_version": self.schema_version,
        }

    def summary(self) -> dict[str, Any]:
        last_message = self.messages[-1].content if self.messages else None
        return {
            "id": self.id,
            "persona_id": self.persona_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            "last_message": last_message,
        }
