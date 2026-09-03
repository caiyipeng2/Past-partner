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

OCR_PURPOSE = "image_ocr"
OCR_AUTHORIZATION_SCOPE = "persona-image-ocr"


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
    analysis_kind: str = "description"

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
            "analysis_kind": self.analysis_kind,
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
        analysis_kind: str = "description",
    ) -> MultimodalAuthorization:
        category = _media_category(data_category)
        operation = _analysis_kind(analysis_kind)
        if operation == "ocr" and category != "image":
            raise ConsentValidationError("unsupported_media_operation", "OCR requires image media")
        required_capability = "ocr" if operation == "ocr" else _MEDIA_CAPABILITIES[category]
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
        if operation == "ocr" and (
            consent.purpose != OCR_PURPOSE
            or consent.authorization_scope != OCR_AUTHORIZATION_SCOPE
        ):
            raise ConsentValidationError(
                "consent_scope_mismatch",
                "OCR requires a dedicated image OCR consent",
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
            analysis_kind=operation,
        )


def _media_category(value: object) -> str:
    if not isinstance(value, str):
        raise ConsentValidationError("unsupported_media_category", "data_category must be a supported media type")
    category = value.strip().casefold()
    if category not in _MEDIA_CAPABILITIES:
        raise ConsentValidationError("unsupported_media_category", "data_category must identify image, audio, or video")
    return category


def _analysis_kind(value: object) -> str:
    if not isinstance(value, str):
        raise ConsentValidationError("unsupported_media_operation", "media analysis operation is not supported")
    operation = value.strip().casefold()
    if operation in {"description", "describe"}:
        return "description"
    if operation == "ocr":
        return operation
    raise ConsentValidationError("unsupported_media_operation", "media analysis operation is not supported")


__all__ = [
    "OCR_AUTHORIZATION_SCOPE",
    "OCR_PURPOSE",
    "MultimodalAuthorization",
    "MultimodalConsentGate",
]
