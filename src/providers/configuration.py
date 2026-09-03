"""Build provider adapters from process secrets without persisting them."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from src.providers.catalog import ProviderCatalog
from src.providers.base import ProviderAdapter
from src.providers.native import AnthropicAdapter, AnthropicConfig, GeminiAdapter, GeminiConfig
from src.providers.openai_compatible import OpenAICompatibleAdapter, OpenAICompatibleConfig
from src.providers.qwen_fine_tuning import QwenFineTuningAdapter, QwenFineTuningConfig


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
    # Local OpenAI-compatible runtimes commonly expose no authentication;
    # endpoint and an explicit model allowlist remain mandatory.
    _EnvironmentProvider(
        "custom_openai",
        "PAST_PARTNER_CUSTOM_OPENAI",
        None,
        key_required=False,
        models_required=True,
    ),
)

_NATIVE_PROVIDERS = (
    _EnvironmentProvider(
        "anthropic",
        "PAST_PARTNER_ANTHROPIC",
        "https://api.anthropic.com",
        ("ANTHROPIC_API_KEY",),
    ),
    _EnvironmentProvider(
        "gemini",
        "PAST_PARTNER_GEMINI",
        "https://generativelanguage.googleapis.com",
        ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ),
)

_QWEN_FINE_TUNING_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"


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
        audio_models = _models(environment.get(f"{definition.prefix}_AUDIO_MODELS"))
        if not audio_models.issubset(allowed_models):
            raise ValueError(f"{definition.prefix}_AUDIO_MODELS must be included in {definition.prefix}_MODELS")
        adapters[definition.provider_id] = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                provider_id=definition.provider_id,
                base_url=base_url,
                api_key=api_key,
                allowed_models=allowed_models,
                media_capabilities={
                    model_id: frozenset({"audio"})
                    for model_id in audio_models
                },
            )
        )
    return adapters


def build_provider_adapters(
    catalog: ProviderCatalog,
    environ: Mapping[str, str] | None = None,
) -> dict[str, ProviderAdapter]:
    """Build all supported runtime adapters while keeping secrets at process scope."""

    environment = os.environ if environ is None else environ
    compatible_adapters = build_openai_compatible_adapters(catalog, environment)
    adapters: dict[str, ProviderAdapter] = dict(compatible_adapters)
    _configure_qwen_fine_tuning(adapters, compatible_adapters, environment)
    for definition in _NATIVE_PROVIDERS:
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
        if definition.provider_id == "anthropic":
            adapters[definition.provider_id] = AnthropicAdapter(
                AnthropicConfig(
                    provider_id=definition.provider_id,
                    base_url=base_url,
                    api_key=api_key or "",
                    allowed_models=allowed_models,
                )
            )
        else:
            adapters[definition.provider_id] = GeminiAdapter(
                GeminiConfig(
                    provider_id=definition.provider_id,
                    base_url=base_url,
                    api_key=api_key or "",
                    allowed_models=allowed_models,
                )
            )
    return adapters


def _configure_qwen_fine_tuning(
    adapters: dict[str, ProviderAdapter],
    compatible_adapters: Mapping[str, OpenAICompatibleAdapter],
    environment: Mapping[str, str],
) -> None:
    """Replace only an explicitly opted-in Qwen chat adapter with its native API."""

    if not _flag(environment.get("PAST_PARTNER_QWEN_FINE_TUNING_ENABLED")):
        return
    chat = compatible_adapters.get("qwen")
    if chat is None:
        return
    configured_models = _models(environment.get("PAST_PARTNER_QWEN_FINE_TUNING_MODELS"))
    fine_tuning_models = configured_models & chat.config.allowed_models
    if not fine_tuning_models:
        return
    native_base_url = (
        _first_value(environment, "PAST_PARTNER_QWEN_FINE_TUNING_BASE_URL")
        or _QWEN_FINE_TUNING_BASE_URL
    )
    _validate_base_url(native_base_url, "qwen fine-tuning")
    adapters["qwen"] = QwenFineTuningAdapter(
        QwenFineTuningConfig(
            provider_id="qwen",
            base_url=native_base_url,
            api_key=chat.config.api_key or "",
            allowed_models=chat.config.allowed_models,
            fine_tuning_models=fine_tuning_models,
            chat_base_url=chat.config.base_url,
            timeout_seconds=chat.config.timeout_seconds,
            media_capabilities=chat.config.media_capabilities,
        )
    )


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


def _flag(value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("PAST_PARTNER_QWEN_FINE_TUNING_ENABLED must be a boolean")


def _validate_base_url(value: str, provider_id: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"invalid base URL for provider {provider_id}")
