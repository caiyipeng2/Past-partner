"""Validated, revocable authorization for third-party media processing."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Mapping
from uuid import uuid4


class ConsentValidationError(ValueError):
    """Raised when a consent record or authorization scope is invalid."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class MediaConsent:
    id: str
    persona_id: str
    provider_id: str
    model_id: str
    data_category: str
    estimated_cost: float
    purpose: str
    authorization_scope: str
    created_at: str
    status: str = "active"
    revoked_at: str | None = None

    @classmethod
    def create(
        cls,
        *,
        persona_id: object,
        provider_id: object,
        model_id: object,
        data_category: object,
        estimated_cost: object,
        purpose: object,
        authorization_scope: object,
        created_at: object,
        consent_id: object = None,
        status: object = "active",
        revoked_at: object = None,
    ) -> "MediaConsent":
        consent_id = _identifier(consent_id, "consent_id") if consent_id is not None else str(uuid4())
        if status != "active":
            raise ConsentValidationError("invalid_consent_status", "new consent must be active")
        if revoked_at is not None:
            raise ConsentValidationError("invalid_consent_status", "active consent cannot have revoked_at")
        if not isinstance(created_at, str) or not created_at.strip():
            raise ConsentValidationError("invalid_created_at", "created_at must be a non-empty string")
        return cls(
            id=consent_id,
            persona_id=_identifier(persona_id, "persona_id"),
            provider_id=_text(provider_id, "provider_id", 128),
            model_id=_text(model_id, "model_id", 256),
            data_category=_text(data_category, "data_category", 64),
            estimated_cost=_cost(estimated_cost),
            purpose=_text(purpose, "purpose", 512),
            authorization_scope=_text(authorization_scope, "authorization_scope", 256),
            created_at=created_at.strip(),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "MediaConsent":
        if not isinstance(value, Mapping):
            raise ConsentValidationError("invalid_consent", "consent must be an object")
        try:
            consent_id = _identifier(value["id"], "consent_id")
            persona_id = _identifier(value["persona_id"], "persona_id")
            provider_id = _text(value["provider_id"], "provider_id", 128)
            model_id = _text(value["model_id"], "model_id", 256)
            data_category = _text(value["data_category"], "data_category", 64)
            estimated_cost = _cost(value["estimated_cost"])
            purpose = _text(value["purpose"], "purpose", 512)
            authorization_scope = _text(value["authorization_scope"], "authorization_scope", 256)
            created_at = _text(value["created_at"], "created_at", 128)
            status = value.get("status", "active")
            revoked_at = value.get("revoked_at")
        except KeyError as exc:
            raise ConsentValidationError("missing_consent_field", f"consent missing {exc.args[0]}") from exc
        if status not in {"active", "revoked"}:
            raise ConsentValidationError("invalid_consent_status", "consent status is unsupported")
        if status == "active" and revoked_at is not None:
            raise ConsentValidationError("invalid_consent_status", "active consent cannot have revoked_at")
        if status == "revoked":
            revoked_at = _text(revoked_at, "revoked_at", 128)
        return cls(
            id=consent_id,
            persona_id=persona_id,
            provider_id=provider_id,
            model_id=model_id,
            data_category=data_category,
            estimated_cost=estimated_cost,
            purpose=purpose,
            authorization_scope=authorization_scope,
            created_at=created_at,
            status=status,
            revoked_at=revoked_at,
        )

    def revoke(self, revoked_at: str) -> "MediaConsent":
        if self.status == "revoked":
            raise ConsentValidationError("consent_already_revoked", "consent is already revoked")
        return replace(self, status="revoked", revoked_at=_text(revoked_at, "revoked_at", 128))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "persona_id": self.persona_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "data_category": self.data_category,
            "estimated_cost": self.estimated_cost,
            "purpose": self.purpose,
            "authorization_scope": self.authorization_scope,
            "created_at": self.created_at,
            "status": self.status,
            "revoked_at": self.revoked_at,
        }


def _identifier(value: object, field_name: str) -> str:
    return _text(value, field_name, 128)


def _text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConsentValidationError(f"invalid_{field_name}", f"{field_name} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum or any(ord(character) < 32 for character in text):
        raise ConsentValidationError(f"invalid_{field_name}", f"{field_name} is not valid metadata")
    return text


def _cost(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConsentValidationError("invalid_estimated_cost", "estimated_cost must be a number")
    if not math.isfinite(float(value)) or float(value) < 0 or float(value) > 1_000_000_000:
        raise ConsentValidationError("invalid_estimated_cost", "estimated_cost is outside the supported range")
    return float(value)
