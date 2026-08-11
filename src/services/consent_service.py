"""Consent lifecycle and exact-scope authorization rules."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import threading
from typing import Iterator

from src.domain.consents import ConsentValidationError, MediaConsent
from src.services.consent_repository import ConsentRepository
from src.services.persona_service import PersonaNotFoundError, PersonaService


class ConsentNotFoundError(LookupError):
    """Raised when a consent is not visible to the requested owner."""


class ConsentService:
    def __init__(self, repository: ConsentRepository, personas: PersonaService):
        self.repository = repository
        self.personas = personas
        # Fine-tuning invokes a remote provider after local authorization. A shared
        # lock lets the service hold only that short handoff window while revoke()
        # waits, so a consent cannot become revoked between its final check and the
        # sensitive transfer.
        self._provider_handoff_lock = threading.RLock()

    def create(
        self,
        owner_id: str,
        persona_id: str,
        provider_id: str,
        model_id: str,
        data_category: str,
        estimated_cost: float,
        purpose: str,
        authorization_scope: str,
    ) -> MediaConsent:
        try:
            self.personas.get(owner_id, persona_id)
        except PersonaNotFoundError as exc:
            raise ConsentValidationError("persona_not_found", "select an existing persona") from exc
        candidate = MediaConsent.create(
            persona_id=persona_id,
            provider_id=provider_id,
            model_id=model_id,
            data_category=data_category,
            estimated_cost=estimated_cost,
            purpose=purpose,
            authorization_scope=authorization_scope,
            created_at=datetime.now(UTC).isoformat(),
        )
        duplicate = next(
            (
                item
                for item in self.repository.list(owner_id, persona_id)
                if item.status == "active" and self._same_scope(item, candidate)
            ),
            None,
        )
        if duplicate is not None:
            raise ConsentValidationError("consent_exists", "an active consent already covers this scope")
        self.repository.save(owner_id, candidate)
        return candidate

    def get(self, owner_id: str, consent_id: str) -> MediaConsent:
        consent = self.repository.get(owner_id, consent_id)
        if consent is None:
            raise ConsentNotFoundError("consent not found")
        return consent

    def list(self, owner_id: str, persona_id: str | None = None) -> list[MediaConsent]:
        return self.repository.list(owner_id, persona_id)

    def revoke(self, owner_id: str, consent_id: str) -> MediaConsent:
        with self.provider_handoff_guard():
            consent = self.get(owner_id, consent_id)
            revoked = consent.revoke(datetime.now(UTC).isoformat())
            self.repository.save(owner_id, revoked)
            return revoked

    @contextmanager
    def provider_handoff_guard(self) -> Iterator[None]:
        """Serialize consent revocation with the final third-party data handoff."""

        with self._provider_handoff_lock:
            yield

    def authorize(
        self,
        owner_id: str,
        consent_id: str,
        *,
        provider_id: str,
        model_id: str,
        data_category: str,
        authorization_scope: str,
    ) -> MediaConsent:
        consent = self.get(owner_id, consent_id)
        if consent.status != "active":
            raise ConsentValidationError("consent_revoked", "consent is revoked")
        expected = {
            "provider_id": provider_id,
            "model_id": model_id,
            "data_category": data_category,
            "authorization_scope": authorization_scope,
        }
        actual = {key: getattr(consent, key) for key in expected}
        if actual != expected:
            raise ConsentValidationError("consent_scope_mismatch", "consent does not cover this provider, model, or data scope")
        return consent

    def delete_for_persona(self, owner_id: str, persona_id: str) -> int:
        return self.repository.delete_for_persona(owner_id, persona_id)

    @staticmethod
    def _same_scope(left: MediaConsent, right: MediaConsent) -> bool:
        return (
            left.provider_id == right.provider_id
            and left.model_id == right.model_id
            and left.data_category == right.data_category
            and left.authorization_scope == right.authorization_scope
        )
