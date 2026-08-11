"""Capability-gated authorization for provider/model media processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domain.consents import ConsentValidationError
from src.providers.catalog import ProviderCatalog
from src.services.consent_service import ConsentService


_MEDIA_CAPABILITIES = {
    "image": "vision",
    "photo": "vision",
    "picture": "vision",
    "vision": "vision",
    "audio": "audio",
    "voice": "audio",
    "sound": "audio",
    "video": "video",
}


@dataclass(frozen=True, slots=True)
class MultimodalAuthorization:
    """An auditable decision proving consent and declared capability matched."""

    authorized: bool
    consent_id: str
    persona_id: str
    provider_id: str
    model_id: str
    data_category: str
    authorization_scope: str
    required_capability: str
    provider_capabilities: tuple[str, ...]
    model_capabilities: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "authorized": self.authorized,
            "consent_id": self.consent_id,
            "persona_id": self.persona_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "data_category": self.data_category,
            "authorization_scope": self.authorization_scope,
            "required_capability": self.required_capability,
            "provider_capabilities": list(self.provider_capabilities),
            "model_capabilities": list(self.model_capabilities),
        }


class MultimodalConsentGate:
    """Combine exact consent scope with provider/model capability metadata."""

    def __init__(self, consents: ConsentService, catalog: ProviderCatalog):
        self.consents = consents
        self.catalog = catalog

    def authorize(
        self,
        owner_id: str,
        consent_id: str,
        *,
        provider_id: str,
        model_id: str,
        data_category: str,
        authorization_scope: str,
    ) -> MultimodalAuthorization:
        category = _media_category(data_category)
        required_capability = _MEDIA_CAPABILITIES[category]
        provider = self.catalog.find_provider(provider_id)
        if provider is None:
            raise ConsentValidationError("unknown_provider", "provider does not exist")
        model = self.catalog.find_model(provider_id, model_id)
        if model is None:
            raise ConsentValidationError("unknown_model", "model is not in the provider catalog")

        model_capabilities = tuple(sorted(set(model.capabilities)))
        provider_capabilities = tuple(sorted(set(provider.capabilities)))
        if required_capability not in model_capabilities:
            raise ConsentValidationError(
                "model_capability_missing",
                f"model does not advertise the required {required_capability} capability",
            )
        if required_capability not in provider_capabilities:
            raise ConsentValidationError(
                "provider_capability_missing",
                f"provider does not advertise the required {required_capability} capability",
            )

        consent = self.consents.authorize(
            owner_id,
            consent_id,
            provider_id=provider_id,
            model_id=model_id,
            data_category=category,
            authorization_scope=authorization_scope,
        )
        return MultimodalAuthorization(
            authorized=True,
            consent_id=consent.id,
            persona_id=consent.persona_id,
            provider_id=provider_id,
            model_id=model_id,
            data_category=category,
            authorization_scope=consent.authorization_scope,
            required_capability=required_capability,
            provider_capabilities=provider_capabilities,
            model_capabilities=model_capabilities,
        )


def _media_category(value: object) -> str:
    if not isinstance(value, str):
        raise ConsentValidationError("unsupported_media_category", "data_category must be a supported media type")
    category = value.strip().casefold()
    if category not in _MEDIA_CAPABILITIES:
        raise ConsentValidationError("unsupported_media_category", "data_category must identify image, audio, or video")
    return category


__all__ = ["MultimodalAuthorization", "MultimodalConsentGate"]
