"""Persistent persona operations used by Web and future mobile clients."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping

from src.domain.personas import Persona
from src.services.persona_repository import PersonaRepository


class PersonaNotFoundError(LookupError):
    """Raised when a server-issued persona identifier does not exist."""


class PersonaService:
    def __init__(self, repository: PersonaRepository):
        self.repository = repository

    def create(
        self,
        owner_id: str,
        display_name: str | None = None,
        relationship_type: str | None = None,
        custom_label: str | None = None,
        *,
        relationship_label: str | None = None,
        preferred_address: str | None = None,
        user_address: str | None = None,
        relationship_description: str | None = None,
        tone_boundaries: Sequence[str] | None = None,
        forbidden_topics: Sequence[str] | None = None,
    ) -> Persona:
        relationship_values = {"father", "mother", "relative", "friend", "partner", "custom"}
        if relationship_type is not None and display_name in relationship_values and relationship_type not in relationship_values:
            custom_label = relationship_type
            relationship_type = display_name
            display_name = owner_id
            owner_id = None
        elif relationship_type is None:
            relationship_type = display_name
            display_name = owner_id
            owner_id = None
        if display_name is None or relationship_type is None:
            raise TypeError("display_name and relationship_type are required")
        persona = Persona.create(
            display_name,
            relationship_type,
            custom_label,
            relationship_label=relationship_label,
            preferred_address=preferred_address,
            user_address=user_address,
            relationship_description=relationship_description,
            tone_boundaries=tone_boundaries,
            forbidden_topics=forbidden_topics,
        )
        self.repository.save(owner_id, persona)
        return persona

    def get(self, owner_id: str, persona_id: str | None = None) -> Persona:
        if persona_id is None:
            persona_id = owner_id
            owner_id = None
        persona = self.repository.get(owner_id, persona_id)
        if persona is None:
            raise PersonaNotFoundError("persona not found")
        return persona

    def update(
        self,
        owner_id: str,
        persona_id: str | Mapping[str, Any],
        changes: Mapping[str, Any] | None = None,
    ) -> Persona:
        if changes is None:
            changes = persona_id
            persona_id = owner_id
            owner_id = None
        if not isinstance(persona_id, str) or not isinstance(changes, Mapping):
            raise TypeError("persona_id and changes are required")
        persona = self.repository.update(owner_id, persona_id, changes)
        if persona is None:
            raise PersonaNotFoundError("persona not found")
        return persona

    def list(self, owner_id: str | None = None) -> list[Persona]:
        return self.repository.list(owner_id)

    def delete(self, owner_id: str, persona_id: str | None = None) -> bool:
        if persona_id is None:
            persona_id = owner_id
            owner_id = None
        deleted = self.repository.delete(owner_id, persona_id)
        if not deleted:
            raise PersonaNotFoundError("persona not found")
        return True
