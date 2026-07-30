"""Build provider adapters from process secrets without persisting them."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from src.providers.catalog import ProviderCatalog
from src.providers.openai_compatible import OpenAICompatibleAdapter, OpenAICompatibleConfig


@dataclass(frozen=True, slots=True)
class _EnvironmentProvider:
    provider_id: str
    prefix: str
    default_base_url: str | None
    key_aliases: tuple[str, ...] = ()
    key_required: bool = True
    models_required: bool = False


_PROVIDERS = (
    _EnvironmentProvider("openai", "PAST_PARTNER_OPENAI", "https://api.openai.com/v1", ("OPENAI_API_KEY",)),
    _EnvironmentProvider("deepseek", "PAST_PARTNER_DEEPSEEK", "https://api.deepseek.com", ("DEEPSEEK_API_KEY",)),
    _EnvironmentProvider("xiaomi_mimo", "PAST_PARTNER_XIAOMI_MIMO", "https://api.xiaomimimo.com/v1", ("MIMO_API_KEY",)),
    _EnvironmentProvider("qwen", "PAST_PARTNER_QWEN", "https://dashscope.aliyuncs.com/compatible-mode/v1", ("DASHSCOPE_API_KEY",)),
    _EnvironmentProvider("ollama", "PAST_PARTNER_OLLAMA", "http://127.0.0.1:11434/v1", key_required=False, models_required=True),
    _EnvironmentProvider("custom_openai", "PAST_PARTNER_CUSTOM_OPENAI", None, models_required=True),
)


def build_openai_compatible_adapters(
    catalog: ProviderCatalog,
    environ: Mapping[str, str] | None = None,
) -> dict[str, OpenAICompatibleAdapter]:
    environment = os.environ if environ is None else environ
    adapters: dict[str, OpenAICompatibleAdapter] = {}
    for definition in _PROVIDERS:
        provider = catalog.find_provider(definition.provider_id)
        if provider is None:
            continue
        api_key = _first_value(
            environment,
            f"{definition.prefix}_API_KEY",
            *definition.key_aliases,
        )
        base_url = _first_value(environment, f"{definition.prefix}_BASE_URL") or definition.default_base_url
        configured_models = _models(environment.get(f"{definition.prefix}_MODELS"))
        allowed_models = configured_models or frozenset(model.id for model in provider.models)

        if definition.key_required and not api_key:
            continue
        if definition.models_required and not configured_models:
            continue
        if not base_url or not allowed_models:
            continue
        _validate_base_url(base_url, definition.provider_id)
        adapters[definition.provider_id] = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                provider_id=definition.provider_id,
                base_url=base_url,
                api_key=api_key,
                allowed_models=allowed_models,
            )
        )
    return adapters


def _first_value(environment: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = environment.get(name)
        if value and value.strip():
            return value.strip()
    return None


def _models(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())


def _validate_base_url(value: str, provider_id: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"invalid base URL for provider {provider_id}")
