"""Persistent persona operations used by Web and future mobile clients."""

from __future__ import annotations

from src.domain.personas import Persona
from src.services.storage import InvalidStorageIdentifier, StorageLayout


class PersonaNotFoundError(LookupError):
    """Raised when a server-issued persona identifier does not exist."""


class PersonaService:
    def __init__(self, storage: StorageLayout):
        self.storage = storage

    def create(
        self,
        display_name: str,
        relationship_type: str,
        custom_label: str | None = None,
    ) -> Persona:
        persona = Persona.create(display_name, relationship_type, custom_label)
        self.storage.write_json("personas", persona.id, persona.to_dict())
        return persona

    def get(self, persona_id: str) -> Persona:
        try:
            value = self.storage.read_json("personas", persona_id)
        except (FileNotFoundError, InvalidStorageIdentifier) as exc:
            raise PersonaNotFoundError("persona not found") from exc
        return Persona.from_dict(value)

    def list(self) -> list[Persona]:
        directory = self.storage.ensure_collection("personas")
        personas: list[Persona] = []
        for path in sorted(directory.glob("*.json")):
            personas.append(Persona.from_dict(self.storage.read_json("personas", path.stem)))
        return personas
