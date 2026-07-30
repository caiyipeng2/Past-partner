"""Transport-neutral provider request and response types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class ChatRequest:
    provider_id: str
    model_id: str
    messages: tuple[ChatMessage, ...]
    temperature: float | None = None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    provider_id: str
    model_id: str
    content: str
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    provider_request_id: str | None = None


class ProviderAdapter(Protocol):
    provider_id: str

    def supports_model(self, model_id: str) -> bool:
        ...

    def chat(self, request: ChatRequest) -> ChatResponse:
        ...


JsonObject = dict[str, Any]
