"""OpenAI-compatible adapter shared by hosted, local, and custom endpoints."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.providers.base import AdapterError, ChatRequest, ChatResponse, JsonObject


Transport = Callable[[str, dict[str, str], JsonObject, float], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    provider_id: str
    base_url: str
    api_key: str | None
    allowed_models: frozenset[str]
    timeout_seconds: float = 60.0


class OpenAICompatibleAdapter:
    def __init__(self, config: OpenAICompatibleConfig, transport: Transport | None = None):
        self.config = config
        self.provider_id = config.provider_id
        self.transport = transport or _urllib_transport

    def supports_model(self, model_id: str) -> bool:
        return model_id in self.config.allowed_models

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


def _urllib_transport(
    url: str,
    headers: dict[str, str],
    body: JsonObject,
    timeout_seconds: float,
) -> Mapping[str, Any]:
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise AdapterError("provider_http_error", f"provider returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise AdapterError("provider_unavailable", "provider could not be reached") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("invalid_provider_response", "provider returned invalid JSON") from exc
