"""Persona identity rules that are independent from HTTP and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4


class PersonaValidationError(ValueError):
    """Raised when a persona cannot satisfy the public identity contract."""


class RelationshipType(str, Enum):
    FATHER = "father"
    MOTHER = "mother"
    RELATIVE = "relative"
    FRIEND = "friend"
    PARTNER = "partner"
    CUSTOM = "custom"


MAX_DISPLAY_NAME_CHARACTERS = 80
MAX_CUSTOM_LABEL_CHARACTERS = 40


@dataclass(frozen=True, slots=True)
class Persona:
    id: str
    display_name: str
    relationship_type: RelationshipType
    custom_label: str | None
    created_at: str

    @classmethod
    def create(
        cls,
        display_name: str,
        relationship_type: str,
        custom_label: str | None = None,
    ) -> "Persona":
        name = _required_text(display_name, "display_name", MAX_DISPLAY_NAME_CHARACTERS)
        try:
            relationship = RelationshipType(relationship_type)
        except (TypeError, ValueError) as exc:
            raise PersonaValidationError("unsupported relationship_type") from exc

        label = (
            _required_text(custom_label, "custom_label", MAX_CUSTOM_LABEL_CHARACTERS)
            if relationship is RelationshipType.CUSTOM
            else None
        )

        return cls(
            id=str(uuid4()),
            display_name=name,
            relationship_type=relationship,
            custom_label=label,
            created_at=datetime.now(UTC).isoformat(),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Persona":
        try:
            relationship = RelationshipType(value["relationship_type"])
            persona_id = _required_text(value["id"], "id")
            display_name = _required_text(
                value["display_name"],
                "display_name",
                MAX_DISPLAY_NAME_CHARACTERS,
            )
            created_at = _required_text(value["created_at"], "created_at")
        except (KeyError, TypeError, ValueError) as exc:
            raise PersonaValidationError("invalid stored persona") from exc

        custom_label = value.get("custom_label")
        if relationship is RelationshipType.CUSTOM:
            custom_label = _required_text(
                custom_label,
                "custom_label",
                MAX_CUSTOM_LABEL_CHARACTERS,
            )
        else:
            custom_label = None

        return cls(persona_id, display_name, relationship, custom_label, created_at)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "relationship_type": self.relationship_type.value,
            "custom_label": self.custom_label,
            "created_at": self.created_at,
        }


def _required_text(value: object, field_name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PersonaValidationError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum or any(ord(character) < 32 for character in text):
        raise PersonaValidationError(f"{field_name} contains invalid text")
    return text
