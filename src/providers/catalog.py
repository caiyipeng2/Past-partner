"""Stable provider metadata without embedding credentials or volatile prices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import json
import math
from typing import Any


class CatalogValidationError(ValueError):
    """Raised when catalog metadata or a cost-estimate request is invalid."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Provider/admin supplied prices; missing values remain explicitly unknown."""

    input_price_per_million_tokens: float | None = None
    output_price_per_million_tokens: float | None = None
    training_price_per_million_tokens: float | None = None
    media_price_per_unit: float | None = None
    media_unit: str = "unit"
    currency: str = "USD"
    source: str = "provider"
    last_refreshed_at: str | None = None

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
        *,
        fallback: "ModelPricing | None" = None,
        source: str = "provider",
    ) -> "ModelPricing":
        if value is None:
            return fallback or cls(source=source)
        if not isinstance(value, Mapping):
            raise CatalogValidationError("invalid_pricing_config", "model pricing must be an object")
        base = fallback or cls(source=source)
        return cls(
            input_price_per_million_tokens=_optional_price(
                value.get("input_price_per_million_tokens", base.input_price_per_million_tokens),
                "input_price_per_million_tokens",
            ),
            output_price_per_million_tokens=_optional_price(
                value.get("output_price_per_million_tokens", base.output_price_per_million_tokens),
                "output_price_per_million_tokens",
            ),
            training_price_per_million_tokens=_optional_price(
                value.get("training_price_per_million_tokens", base.training_price_per_million_tokens),
                "training_price_per_million_tokens",
            ),
            media_price_per_unit=_optional_price(
                value.get("media_price_per_unit", base.media_price_per_unit),
                "media_price_per_unit",
            ),
            media_unit=_metadata_text(value.get("media_unit", base.media_unit), "media_unit", 64),
            currency=_metadata_text(value.get("currency", base.currency), "currency", 16),
            source=_metadata_text(value.get("source", base.source), "source", 64),
            last_refreshed_at=_optional_text(
                value.get("last_refreshed_at", base.last_refreshed_at),
                "last_refreshed_at",
                128,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_price_per_million_tokens": self.input_price_per_million_tokens,
            "output_price_per_million_tokens": self.output_price_per_million_tokens,
            "training_price_per_million_tokens": self.training_price_per_million_tokens,
            "media_price_per_unit": self.media_price_per_unit,
            "media_unit": self.media_unit,
            "currency": self.currency,
            "source": self.source,
            "last_refreshed_at": self.last_refreshed_at,
        }


@dataclass(frozen=True, slots=True)
class CostEstimate:
    provider_id: str
    model_id: str
    currency: str
    input_tokens: int
    output_tokens: int
    media_units: float
    input_cost: float
    output_cost: float
    media_cost: float
    total_cost: float
    price_last_refreshed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "currency": self.currency,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "media_units": self.media_units,
            "input_cost": self.input_cost,
            "output_cost": self.output_cost,
            "media_cost": self.media_cost,
            "estimated_cost": self.total_cost,
            "price_last_refreshed_at": self.price_last_refreshed_at,
        }


@dataclass(frozen=True, slots=True)
class TrainingCostEstimate:
    """A training-token estimate using explicitly supplied model metadata."""

    provider_id: str
    model_id: str
    currency: str
    training_tokens: int
    training_price_per_million_tokens: float
    estimated_cost: float
    price_last_refreshed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "currency": self.currency,
            "training_tokens": self.training_tokens,
            "training_price_per_million_tokens": self.training_price_per_million_tokens,
            "estimated_cost": self.estimated_cost,
            "price_last_refreshed_at": self.price_last_refreshed_at,
        }


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    id: str
    display_name: str
    capabilities: tuple[str, ...]
    pricing_source: str = "provider"
    context_length: int | None = None
    regions: tuple[str, ...] = ()
    privacy_metadata: tuple[str, ...] = ()
    pricing: ModelPricing = field(default_factory=ModelPricing)

    def to_dict(self) -> dict[str, Any]:
        pricing = self.pricing.to_dict()
        return {
            "id": self.id,
            "display_name": self.display_name,
            "capabilities": list(self.capabilities),
            "pricing_source": self.pricing_source,
            "context_length": self.context_length,
            "regions": list(self.regions),
            "privacy_metadata": list(self.privacy_metadata),
            "pricing": pricing,
            # Keep the original flat keys for clients that already consume v1.
            "input_price": pricing["input_price_per_million_tokens"],
            "output_price": pricing["output_price_per_million_tokens"],
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

    def with_pricing(self, values: Mapping[str, Mapping[str, Any]]) -> "ProviderCatalog":
        if not isinstance(values, Mapping):
            raise CatalogValidationError("invalid_pricing_config", "model pricing configuration must be an object")
        updates: dict[tuple[str, str], ModelDefinition] = {}
        for key, metadata in values.items():
            if not isinstance(key, str) or key.count("/") != 1:
                raise CatalogValidationError("invalid_pricing_config", "pricing keys must be provider_id/model_id")
            provider_id, model_id = key.split("/", 1)
            model = self.find_model(provider_id, model_id)
            if model is None:
                raise CatalogValidationError("unknown_model", "pricing references an unknown provider or model")
            if not isinstance(metadata, Mapping):
                raise CatalogValidationError("invalid_pricing_config", "model pricing must be an object")
            pricing = ModelPricing.from_mapping(metadata, fallback=model.pricing, source=model.pricing_source)
            context_length = _optional_positive_int(
                metadata.get("context_length", model.context_length), "context_length"
            )
            regions = _metadata_tuple(metadata.get("regions", model.regions), "regions")
            privacy = _metadata_tuple(
                metadata.get("privacy_metadata", model.privacy_metadata), "privacy_metadata"
            )
            updates[(provider_id, model_id)] = replace(
                model,
                pricing_source=pricing.source,
                context_length=context_length,
                regions=regions,
                privacy_metadata=privacy,
                pricing=pricing,
            )

        providers: list[ProviderDefinition] = []
        for provider in self.providers():
            providers.append(
                replace(
                    provider,
                    models=tuple(
                        updates.get((provider.id, model.id), model)
                        for model in provider.models
                    ),
                )
            )
        return ProviderCatalog(tuple(providers))

    def with_pricing_json(self, raw: str | None) -> "ProviderCatalog":
        if raw is None or not raw.strip():
            return self
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CatalogValidationError("invalid_pricing_config", "model pricing JSON is invalid") from exc
        return self.with_pricing(values)

    def estimate_cost(
        self,
        provider_id: str,
        model_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        media_units: float = 0,
    ) -> CostEstimate:
        provider = self.find_provider(provider_id)
        if provider is None:
            raise CatalogValidationError("unknown_provider", "provider does not exist")
        model = self.find_model(provider_id, model_id)
        if model is None:
            raise CatalogValidationError("unknown_model", "model is not in the provider catalog")
        input_tokens = _usage_int(input_tokens, "input_tokens")
        output_tokens = _usage_int(output_tokens, "output_tokens")
        media_units = _usage_number(media_units, "media_units")
        pricing = model.pricing
        if pricing.input_price_per_million_tokens is None or pricing.output_price_per_million_tokens is None:
            raise CatalogValidationError("pricing_unavailable", "input or output price is unavailable")
        if media_units > 0 and pricing.media_price_per_unit is None:
            raise CatalogValidationError("pricing_unavailable", "media price is unavailable")
        input_cost = input_tokens / 1_000_000 * pricing.input_price_per_million_tokens
        output_cost = output_tokens / 1_000_000 * pricing.output_price_per_million_tokens
        media_cost = media_units * (pricing.media_price_per_unit or 0)
        return CostEstimate(
            provider_id=provider_id,
            model_id=model_id,
            currency=pricing.currency,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            media_units=media_units,
            input_cost=input_cost,
            output_cost=output_cost,
            media_cost=media_cost,
            total_cost=input_cost + output_cost + media_cost,
            price_last_refreshed_at=pricing.last_refreshed_at,
        )

    def estimate_training_cost(
        self,
        provider_id: str,
        model_id: str,
        *,
        training_tokens: int,
    ) -> TrainingCostEstimate:
        provider = self.find_provider(provider_id)
        if provider is None:
            raise CatalogValidationError("unknown_provider", "provider does not exist")
        model = self.find_model(provider_id, model_id)
        if model is None:
            raise CatalogValidationError("unknown_model", "model is not in the provider catalog")
        training_tokens = _usage_int(training_tokens, "training_tokens")
        pricing = model.pricing
        if pricing.training_price_per_million_tokens is None:
            raise CatalogValidationError("pricing_unavailable", "training price is unavailable")
        return TrainingCostEstimate(
            provider_id=provider_id,
            model_id=model_id,
            currency=pricing.currency,
            training_tokens=training_tokens,
            training_price_per_million_tokens=pricing.training_price_per_million_tokens,
            estimated_cost=training_tokens / 1_000_000 * pricing.training_price_per_million_tokens,
            price_last_refreshed_at=pricing.last_refreshed_at,
        )

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
                    ModelDefinition(
                        model_id,
                        model_id,
                        ("text", *provider.capabilities),
                        pricing_source=provider.pricing_source,
                        pricing=ModelPricing(source=provider.pricing_source),
                    )
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
    pricing_source = "local" if credential_mode == "local" else "provider"
    return ProviderDefinition(
        id=provider_id,
        display_name=display_name,
        api_style=api_style,
        capabilities=capabilities,
        credential_mode=credential_mode,
        pricing_source=pricing_source,
        models=tuple(
            ModelDefinition(
                model,
                model,
                ("text", *capabilities),
                pricing_source=pricing_source,
                pricing=ModelPricing(source=pricing_source),
            )
            for model in models
        ),
        model_discovery=discovery,
    )


def _optional_price(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogValidationError("invalid_pricing_config", f"{field_name} must be a number")
    price = float(value)
    if not math.isfinite(price) or price < 0 or price > 1_000_000_000:
        raise CatalogValidationError("invalid_pricing_config", f"{field_name} is outside the supported range")
    return price


def _metadata_text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise CatalogValidationError("invalid_pricing_config", f"{field_name} must be a bounded string")
    return value.strip()


def _optional_text(value: object, field_name: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _metadata_text(value, field_name, maximum)


def _optional_positive_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 10_000_000_000:
        raise CatalogValidationError("invalid_pricing_config", f"{field_name} must be a positive integer")
    return value


def _metadata_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise CatalogValidationError("invalid_pricing_config", f"{field_name} must be a list")
    return tuple(_metadata_text(item, field_name, 128) for item in value)


def _usage_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10**12:
        raise CatalogValidationError("invalid_usage", f"{field_name} must be a non-negative integer")
    return value


def _usage_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogValidationError("invalid_usage", f"{field_name} must be a non-negative number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 10**12:
        raise CatalogValidationError("invalid_usage", f"{field_name} is outside the supported range")
    return number
