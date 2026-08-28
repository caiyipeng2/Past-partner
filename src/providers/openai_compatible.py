"""OpenAI-compatible adapter shared by hosted, local, and custom endpoints."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
import socket
from typing import Any

from src.providers.base import (
    AdapterError,
    ChatRequest,
    ChatResponse,
    JsonObject,
    MediaAnalysisRequest,
    MediaAnalysisResult,
)
from src.providers.transport import JsonTransport, urllib_json_transport


Transport = JsonTransport


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    provider_id: str
    base_url: str
    api_key: str | None
    allowed_models: frozenset[str]
    timeout_seconds: float = 60.0
    max_media_bytes: int = 32 * 1024**2

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(self.max_media_bytes, bool) or not isinstance(self.max_media_bytes, int) or self.max_media_bytes <= 0:
            raise ValueError("max_media_bytes must be a positive integer")


class OpenAICompatibleAdapter:
    def __init__(self, config: OpenAICompatibleConfig, transport: Transport | None = None):
        self.config = config
        self.provider_id = config.provider_id
        self.transport = transport or urllib_json_transport

    def supports_model(self, model_id: str) -> bool:
        return model_id in self.config.allowed_models

    def supports_media(self, model_id: str, media_category: str) -> bool:
        """The first compatible media transport intentionally supports images only."""

        return self.supports_model(model_id) and media_category == "image"

    def chat(self, request: ChatRequest) -> ChatResponse:
        if not self.supports_model(request.model_id):
            raise AdapterError("unknown_model", "model is not allowed by this adapter")
        if not request.messages:
            raise AdapterError("invalid_messages", "at least one chat message is required")

        body: JsonObject = {
            "model": request.model_id,
            "messages": [message.to_dict() for message in request.messages],
            "stream": False,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        endpoint = f"{self.config.base_url.rstrip('/')}/chat/completions"
        payload = self.transport(endpoint, headers, body, self.config.timeout_seconds)
        try:
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterError("invalid_provider_response", "provider response has no assistant message") from exc
        if not isinstance(content, str):
            raise AdapterError("invalid_provider_response", "provider content is not text")

        usage = payload.get("usage")
        normalized_usage = None
        if isinstance(usage, Mapping):
            normalized_usage = {
                key: int(usage[key])
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(usage.get(key), int)
            }
        return ChatResponse(
            provider_id=self.provider_id,
            model_id=request.model_id,
            content=content,
            finish_reason=choice.get("finish_reason") if isinstance(choice, Mapping) else None,
            usage=normalized_usage,
            provider_request_id=str(payload["id"]) if payload.get("id") is not None else None,
        )

    def analyze_media(self, request: MediaAnalysisRequest) -> MediaAnalysisResult:
        if request.provider_id != self.provider_id:
            raise AdapterError("invalid_provider_request", "media request provider does not match the adapter")
        media_type = _image_media_type(request.media_type)
        if not self.supports_media(request.model_id, "image"):
            raise AdapterError("capability_not_supported", "this adapter supports image analysis only")
        if not isinstance(request.prompt, str) or not request.prompt.strip():
            raise AdapterError("invalid_prompt", "analysis prompt is required")
        try:
            size = request.media_path.stat().st_size
            if size < 0 or size > self.config.max_media_bytes:
                raise AdapterError("media_too_large", "media exceeds the configured analysis size limit")
            with request.media_path.open("rb") as source:
                raw_media = source.read(self.config.max_media_bytes + 1)
        except AdapterError:
            raise
        except OSError as exc:
            raise AdapterError("media_unavailable", "media source is unavailable") from exc
        if len(raw_media) > self.config.max_media_bytes:
            raise AdapterError("media_too_large", "media exceeds the configured analysis size limit")
        encoded = base64.b64encode(raw_media)

        body: JsonObject = {
            "model": request.model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": request.prompt.strip()},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{encoded.decode('ascii')}"
                            },
                        },
                    ],
                }
            ],
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        endpoint = f"{self.config.base_url.rstrip('/')}/chat/completions"
        try:
            payload = self.transport(endpoint, headers, body, self.config.timeout_seconds)
        except AdapterError:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise AdapterError("provider_timeout", "provider request timed out") from exc
        except OSError as exc:
            raise AdapterError("provider_unavailable", "provider could not be reached") from exc

        try:
            choice = payload["choices"][0]
            message = choice["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterError("invalid_provider_response", "provider response has no media description") from exc
        description = _analysis_description(content)
        if not description:
            raise AdapterError("invalid_provider_response", "provider response has no media description")
        usage = payload.get("usage")
        normalized_usage = None
        if isinstance(usage, Mapping):
            normalized_usage = {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(usage.get(key), int) and not isinstance(usage.get(key), bool)
            }
        return MediaAnalysisResult(
            provider_id=self.provider_id,
            model_id=request.model_id,
            media_type=request.media_type,
            description=description,
            usage=normalized_usage,
            provider_request_id=str(payload["id"]) if payload.get("id") is not None else None,
        )


def _image_media_type(value: object) -> str:
    if not isinstance(value, str):
        raise AdapterError("capability_not_supported", "this adapter supports image analysis only")
    media_type = value.split(";", 1)[0].strip().lower()
    if not media_type.startswith("image/"):
        raise AdapterError("capability_not_supported", "this adapter supports image analysis only")
    return media_type


def _analysis_description(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for part in value:
        if isinstance(part, Mapping) and part.get("type") == "text" and isinstance(part.get("text"), str):
            parts.append(part["text"])
    return "".join(parts).strip()
