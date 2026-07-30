"""Stable provider metadata without embedding credentials or volatile prices."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    id: str
    display_name: str
    capabilities: tuple[str, ...]
    pricing_source: str = "provider"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "capabilities": list(self.capabilities),
            "pricing_source": self.pricing_source,
            "input_price": None,
            "output_price": None,
        }


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    id: str
    display_name: str
    api_style: str
    capabilities: tuple[str, ...]
    credential_mode: str
    pricing_source: str
    models: tuple[ModelDefinition, ...]
    configured: bool = False
    model_discovery: str = "catalog"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "api_style": self.api_style,
            "capabilities": list(self.capabilities),
            "credential_mode": self.credential_mode,
            "pricing_source": self.pricing_source,
            "configured": self.configured,
            "model_discovery": self.model_discovery,
            "models": [model.to_dict() for model in self.models],
        }


class ProviderCatalog:
    def __init__(self, providers: tuple[ProviderDefinition, ...]):
        self._providers = {provider.id: provider for provider in providers}

    @classmethod
    def default(cls) -> "ProviderCatalog":
        chat = ("chat", "streaming")
        multimodal = ("chat", "streaming", "vision")
        return cls(
            (
                _provider("openai", "OpenAI", "openai", multimodal, "byok", ("gpt-4.1-mini",)),
                _provider("anthropic", "Anthropic", "anthropic", multimodal, "byok", ("claude-sonnet-4-5",)),
                _provider("gemini", "Google Gemini", "gemini", multimodal, "byok", ("gemini-2.5-flash",)),
                _provider("deepseek", "DeepSeek", "openai", chat, "byok", ("deepseek-v4-flash", "deepseek-v4-pro")),
                _provider("xiaomi_mimo", "Xiaomi MiMo", "openai", multimodal, "byok", ("mimo-v2.5-pro",)),
                _provider("qwen", "Alibaba Qwen", "openai", multimodal, "byok", ("qwen3.7-plus", "qwen3.7-max")),
                _provider("ollama", "Ollama", "openai", chat, "local", (), discovery="runtime"),
                _provider("custom_openai", "Custom OpenAI-compatible", "openai", chat, "custom", (), discovery="configured"),
                _provider("custom_http", "Custom HTTP", "custom", ("chat",), "custom", (), discovery="configured"),
            )
        )

    def providers(self) -> tuple[ProviderDefinition, ...]:
        return tuple(self._providers.values())

    def provider(self, provider_id: str) -> ProviderDefinition:
        return self._providers[provider_id]

    def find_provider(self, provider_id: str) -> ProviderDefinition | None:
        return self._providers.get(provider_id)

    def find_model(self, provider_id: str, model_id: str) -> ModelDefinition | None:
        provider = self.find_provider(provider_id)
        if provider is None:
            return None
        return next((model for model in provider.models if model.id == model_id), None)

    def to_dict(self) -> list[dict[str, Any]]:
        return [provider.to_dict() for provider in self.providers()]

    def with_configured(
        self,
        provider_ids: set[str],
        runtime_models: dict[str, frozenset[str]] | None = None,
    ) -> "ProviderCatalog":
        runtime_models = runtime_models or {}
        providers: list[ProviderDefinition] = []
        for provider in self.providers():
            models = provider.models
            if provider.id in runtime_models and provider.model_discovery != "catalog":
                models = tuple(
                    ModelDefinition(model_id, model_id, provider.capabilities)
                    for model_id in sorted(runtime_models[provider.id])
                )
            providers.append(
                replace(
                    provider,
                    configured=provider.id in provider_ids,
                    models=models,
                )
            )
        return ProviderCatalog(tuple(providers))


def _provider(
    provider_id: str,
    display_name: str,
    api_style: str,
    capabilities: tuple[str, ...],
    credential_mode: str,
    models: tuple[str, ...],
    discovery: str = "catalog",
) -> ProviderDefinition:
    return ProviderDefinition(
        id=provider_id,
        display_name=display_name,
        api_style=api_style,
        capabilities=capabilities,
        credential_mode=credential_mode,
        pricing_source="local" if credential_mode == "local" else "provider",
        models=tuple(ModelDefinition(model, model, capabilities) for model in models),
        model_discovery=discovery,
    )
