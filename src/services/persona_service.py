"""Persistent persona operations used by Web and future mobile clients."""

from __future__ import annotations

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
        persona = Persona.create(display_name, relationship_type, custom_label)
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

    def list(self, owner_id: str | None = None) -> list[Persona]:
        return self.repository.list(owner_id)
