"""Native text chat adapters for providers without an OpenAI-compatible API."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

from src.providers.base import AdapterError, ChatRequest, ChatResponse, JsonObject
from src.providers.transport import JsonTransport, urllib_json_transport


DEFAULT_MAX_OUTPUT_TOKENS = 1024


@dataclass(frozen=True, slots=True)
class AnthropicConfig:
    provider_id: str
    base_url: str
    api_key: str
    allowed_models: frozenset[str]
    timeout_seconds: float = 60.0
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS


class AnthropicAdapter:
    def __init__(self, config: AnthropicConfig, transport: JsonTransport | None = None):
        self.config = config
        self.provider_id = config.provider_id
        self.transport = transport or urllib_json_transport

    def supports_model(self, model_id: str) -> bool:
        return model_id in self.config.allowed_models

    def chat(self, request: ChatRequest) -> ChatResponse:
        if not self.supports_model(request.model_id):
            raise AdapterError("unknown_model", "model is not allowed by this adapter")
        if not request.messages:
            raise AdapterError("invalid_messages", "at least one chat message is required")
        system_messages = [message.content for message in request.messages if message.role == "system"]
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
            if message.role != "system"
        ]
        if not messages:
            raise AdapterError("invalid_messages", "at least one non-system chat message is required")
        body: JsonObject = {
            "model": request.model_id,
            "max_tokens": self.config.max_output_tokens,
            "messages": messages,
        }
        if system_messages:
            body["system"] = "\n\n".join(system_messages)
        if request.temperature is not None:
            body["temperature"] = request.temperature
        payload = self.transport(
            f"{self.config.base_url.rstrip('/')}/v1/messages",
            {
                "Content-Type": "application/json",
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
            },
            body,
            self.config.timeout_seconds,
        )
        content = _text_from_anthropic(payload)
        usage = _anthropic_usage(payload.get("usage"))
        return ChatResponse(
            provider_id=self.provider_id,
            model_id=request.model_id,
            content=content,
            finish_reason=_optional_string(payload.get("stop_reason")),
            usage=usage,
            provider_request_id=_optional_string(payload.get("id")),
        )


@dataclass(frozen=True, slots=True)
class GeminiConfig:
    provider_id: str
    base_url: str
    api_key: str
    allowed_models: frozenset[str]
    timeout_seconds: float = 60.0


class GeminiAdapter:
    def __init__(self, config: GeminiConfig, transport: JsonTransport | None = None):
        self.config = config
        self.provider_id = config.provider_id
        self.transport = transport or urllib_json_transport

    def supports_model(self, model_id: str) -> bool:
        return model_id in self.config.allowed_models

    def chat(self, request: ChatRequest) -> ChatResponse:
        if not self.supports_model(request.model_id):
            raise AdapterError("unknown_model", "model is not allowed by this adapter")
        if not request.messages:
            raise AdapterError("invalid_messages", "at least one chat message is required")
        system_messages = [message.content for message in request.messages if message.role == "system"]
        contents = [
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
            for message in request.messages
            if message.role != "system"
        ]
        if not contents:
            raise AdapterError("invalid_messages", "at least one non-system chat message is required")
        body: JsonObject = {"contents": contents}
        if system_messages:
            body["system_instruction"] = {"parts": [{"text": "\n\n".join(system_messages)}]}
        if request.temperature is not None:
            body["generationConfig"] = {"temperature": request.temperature}
        endpoint = (
            f"{self.config.base_url.rstrip('/')}/v1beta/models/"
            f"{quote(request.model_id, safe='-_.')}:generateContent"
        )
        payload = self.transport(
            endpoint,
            {"Content-Type": "application/json", "x-goog-api-key": self.config.api_key},
            body,
            self.config.timeout_seconds,
        )
        candidate = _first_mapping(payload.get("candidates"))
        content = _text_from_gemini(candidate)
        usage = _gemini_usage(payload.get("usageMetadata"))
        return ChatResponse(
            provider_id=self.provider_id,
            model_id=request.model_id,
            content=content,
            finish_reason=_optional_string(candidate.get("finishReason")),
            usage=usage,
            provider_request_id=_optional_string(payload.get("responseId")),
        )


def _text_from_anthropic(payload: Mapping[str, object]) -> str:
    blocks = payload.get("content")
    if not isinstance(blocks, list):
        raise AdapterError("invalid_provider_response", "provider response has no assistant message")
    text = "".join(
        item.get("text", "")
        for item in blocks
        if isinstance(item, Mapping) and item.get("type") == "text" and isinstance(item.get("text"), str)
    )
    if not text:
        raise AdapterError("invalid_provider_response", "provider response has no assistant message")
    return text


def _text_from_gemini(candidate: Mapping[str, object]) -> str:
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, Mapping) else None
    if not isinstance(parts, list):
        raise AdapterError("invalid_provider_response", "provider response has no assistant message")
    text = "".join(
        item.get("text", "")
        for item in parts
        if isinstance(item, Mapping) and isinstance(item.get("text"), str)
    )
    if not text:
        raise AdapterError("invalid_provider_response", "provider response has no assistant message")
    return text


def _first_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        return value[0]
    raise AdapterError("invalid_provider_response", "provider response has no assistant message")


def _anthropic_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    return _usage(
        value.get("input_tokens"),
        value.get("output_tokens"),
    )


def _gemini_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    return _usage(
        value.get("promptTokenCount"),
        value.get("candidatesTokenCount"),
        value.get("totalTokenCount"),
    )


def _usage(input_tokens: object, output_tokens: object, total_tokens: object | None = None) -> dict[str, int] | None:
    if not isinstance(input_tokens, int) or isinstance(input_tokens, bool):
        return None
    if not isinstance(output_tokens, int) or isinstance(output_tokens, bool):
        return None
    total = total_tokens if isinstance(total_tokens, int) and not isinstance(total_tokens, bool) else input_tokens + output_tokens
    return {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": total,
    }


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
