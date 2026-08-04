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
        display_name: str,
        relationship_type: str,
        custom_label: str | None = None,
    ) -> Persona:
        persona = Persona.create(display_name, relationship_type, custom_label)
        self.repository.save(persona)
        return persona

    def get(self, persona_id: str) -> Persona:
        persona = self.repository.get(persona_id)
        if persona is None:
            raise PersonaNotFoundError("persona not found")
        return persona

    def list(self) -> list[Persona]:
        return self.repository.list()
