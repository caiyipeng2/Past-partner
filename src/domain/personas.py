"""Persona identity rules that are independent from HTTP and persistence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
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
MAX_ADDRESS_CHARACTERS = 80
MAX_RELATIONSHIP_DESCRIPTION_CHARACTERS = 2_000
MAX_BOUNDARY_ITEMS = 32
MAX_BOUNDARY_ITEM_CHARACTERS = 120
CURRENT_PERSONA_SCHEMA_VERSION = 1
_PERSONA_UPDATE_FIELDS = frozenset(
    {
        "display_name",
        "relationship_type",
        "custom_label",
        "relationship_label",
        "preferred_address",
        "user_address",
        "relationship_description",
        "tone_boundaries",
        "forbidden_topics",
    }
)


@dataclass(frozen=True, slots=True)
class Persona:
    id: str
    display_name: str
    relationship_type: RelationshipType
    custom_label: str | None
    created_at: str
    preferred_address: str | None = None
    user_address: str | None = None
    relationship_description: str | None = None
    tone_boundaries: tuple[str, ...] = ()
    forbidden_topics: tuple[str, ...] = ()
    updated_at: str | None = None
    schema_version: int = CURRENT_PERSONA_SCHEMA_VERSION

    @property
    def relationship_label(self) -> str | None:
        """Return the design-document name while preserving the old API field."""
        return self.custom_label

    @classmethod
    def create(
        cls,
        display_name: str,
        relationship_type: str,
        custom_label: str | None = None,
        *,
        relationship_label: str | None = None,
        preferred_address: str | None = None,
        user_address: str | None = None,
        relationship_description: str | None = None,
        tone_boundaries: Sequence[str] | None = None,
        forbidden_topics: Sequence[str] | None = None,
    ) -> "Persona":
        name = _required_text(display_name, "display_name", MAX_DISPLAY_NAME_CHARACTERS)
        try:
            relationship = RelationshipType(relationship_type)
        except (TypeError, ValueError) as exc:
            raise PersonaValidationError("unsupported relationship_type") from exc

        if relationship_label is not None and custom_label is not None:
            left = _required_text(custom_label, "custom_label", MAX_CUSTOM_LABEL_CHARACTERS)
            right = _required_text(
                relationship_label,
                "relationship_label",
                MAX_CUSTOM_LABEL_CHARACTERS,
            )
            if left != right:
                raise PersonaValidationError("custom_label and relationship_label conflict")
            custom_label = left
        elif relationship_label is not None:
            custom_label = relationship_label

        label = (
            _required_text(custom_label, "custom_label", MAX_CUSTOM_LABEL_CHARACTERS)
            if relationship is RelationshipType.CUSTOM
            else None
        )
        created_at = datetime.now(UTC).isoformat()

        return cls(
            id=str(uuid4()),
            display_name=name,
            relationship_type=relationship,
            custom_label=label,
            created_at=created_at,
            preferred_address=_optional_text(
                preferred_address, "preferred_address", MAX_ADDRESS_CHARACTERS
            ),
            user_address=_optional_text(user_address, "user_address", MAX_ADDRESS_CHARACTERS),
            relationship_description=_optional_text(
                relationship_description,
                "relationship_description",
                MAX_RELATIONSHIP_DESCRIPTION_CHARACTERS,
            ),
            tone_boundaries=_text_list(tone_boundaries, "tone_boundaries"),
            forbidden_topics=_text_list(forbidden_topics, "forbidden_topics"),
            updated_at=created_at,
            schema_version=CURRENT_PERSONA_SCHEMA_VERSION,
        )

    def update(self, changes: Mapping[str, Any]) -> "Persona":
        if not isinstance(changes, Mapping) or not changes:
            raise PersonaValidationError("persona update must be a non-empty object")
        unknown = set(changes) - _PERSONA_UPDATE_FIELDS
        if unknown:
            raise PersonaValidationError("unsupported persona update field")

        display_name = (
            _required_text(changes["display_name"], "display_name", MAX_DISPLAY_NAME_CHARACTERS)
            if "display_name" in changes
            else self.display_name
        )
        if "relationship_type" in changes:
            try:
                relationship = RelationshipType(changes["relationship_type"])
            except (TypeError, ValueError) as exc:
                raise PersonaValidationError("unsupported relationship_type") from exc
        else:
            relationship = self.relationship_type

        label_present = "custom_label" in changes or "relationship_label" in changes
        custom_label = self.custom_label
        if label_present:
            if "custom_label" in changes and "relationship_label" in changes:
                left = _required_text(
                    changes["custom_label"],
                    "custom_label",
                    MAX_CUSTOM_LABEL_CHARACTERS,
                )
                right = _required_text(
                    changes["relationship_label"],
                    "relationship_label",
                    MAX_CUSTOM_LABEL_CHARACTERS,
                )
                if left != right:
                    raise PersonaValidationError("custom_label and relationship_label conflict")
                custom_label = left
            else:
                custom_label = changes.get("relationship_label", changes.get("custom_label"))
        if relationship is RelationshipType.CUSTOM:
            custom_label = _required_text(
                custom_label,
                "custom_label",
                MAX_CUSTOM_LABEL_CHARACTERS,
            )
        else:
            custom_label = None

        return replace(
            self,
            display_name=display_name,
            relationship_type=relationship,
            custom_label=custom_label,
            preferred_address=(
                _optional_text(changes["preferred_address"], "preferred_address", MAX_ADDRESS_CHARACTERS)
                if "preferred_address" in changes
                else self.preferred_address
            ),
            user_address=(
                _optional_text(changes["user_address"], "user_address", MAX_ADDRESS_CHARACTERS)
                if "user_address" in changes
                else self.user_address
            ),
            relationship_description=(
                _optional_text(
                    changes["relationship_description"],
                    "relationship_description",
                    MAX_RELATIONSHIP_DESCRIPTION_CHARACTERS,
                )
                if "relationship_description" in changes
                else self.relationship_description
            ),
            tone_boundaries=(
                _text_list(changes["tone_boundaries"], "tone_boundaries")
                if "tone_boundaries" in changes
                else self.tone_boundaries
            ),
            forbidden_topics=(
                _text_list(changes["forbidden_topics"], "forbidden_topics")
                if "forbidden_topics" in changes
                else self.forbidden_topics
            ),
            updated_at=datetime.now(UTC).isoformat(),
            schema_version=CURRENT_PERSONA_SCHEMA_VERSION,
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

        schema_version = value.get("schema_version", CURRENT_PERSONA_SCHEMA_VERSION)
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != CURRENT_PERSONA_SCHEMA_VERSION
        ):
            raise PersonaValidationError("unsupported persona schema_version")

        relationship_label = value.get("relationship_label")
        legacy_custom_label = value.get("custom_label")
        if relationship_label is not None and legacy_custom_label is not None:
            try:
                normalized_relationship_label = _required_text(
                    relationship_label,
                    "relationship_label",
                    MAX_CUSTOM_LABEL_CHARACTERS,
                )
                normalized_custom_label = _required_text(
                    legacy_custom_label,
                    "custom_label",
                    MAX_CUSTOM_LABEL_CHARACTERS,
                )
                if normalized_relationship_label != normalized_custom_label:
                    raise PersonaValidationError("custom_label and relationship_label conflict")
            except PersonaValidationError as exc:
                raise PersonaValidationError("invalid stored persona") from exc
        custom_label = relationship_label if relationship_label is not None else legacy_custom_label
        if relationship is RelationshipType.CUSTOM:
            custom_label = _required_text(
                custom_label,
                "custom_label",
                MAX_CUSTOM_LABEL_CHARACTERS,
            )
        else:
            custom_label = None

        try:
            updated_at = _required_text(value.get("updated_at", created_at), "updated_at")
            preferred_address = _optional_text(
                value.get("preferred_address"), "preferred_address", MAX_ADDRESS_CHARACTERS
            )
            user_address = _optional_text(
                value.get("user_address"), "user_address", MAX_ADDRESS_CHARACTERS
            )
            relationship_description = _optional_text(
                value.get("relationship_description"),
                "relationship_description",
                MAX_RELATIONSHIP_DESCRIPTION_CHARACTERS,
            )
            tone_boundaries = _text_list(value.get("tone_boundaries"), "tone_boundaries")
            forbidden_topics = _text_list(value.get("forbidden_topics"), "forbidden_topics")
        except PersonaValidationError as exc:
            raise PersonaValidationError("invalid stored persona") from exc

        return cls(
            persona_id,
            display_name,
            relationship,
            custom_label,
            created_at,
            preferred_address,
            user_address,
            relationship_description,
            tone_boundaries,
            forbidden_topics,
            updated_at,
            schema_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "relationship_type": self.relationship_type.value,
            "custom_label": self.custom_label,
            "relationship_label": self.relationship_label,
            "preferred_address": self.preferred_address,
            "user_address": self.user_address,
            "relationship_description": self.relationship_description,
            "tone_boundaries": list(self.tone_boundaries),
            "forbidden_topics": list(self.forbidden_topics),
            "created_at": self.created_at,
            "updated_at": self.updated_at or self.created_at,
            "schema_version": self.schema_version,
        }


def _required_text(value: object, field_name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PersonaValidationError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum or any(ord(character) < 32 for character in text):
        raise PersonaValidationError(f"{field_name} contains invalid text")
    return text


def _optional_text(value: object, field_name: str, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PersonaValidationError(f"{field_name} must be a string or null")
    return _required_text(value, field_name, maximum)


def _text_list(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise PersonaValidationError(f"{field_name} must be a list of strings")
    if len(value) > MAX_BOUNDARY_ITEMS:
        raise PersonaValidationError(f"{field_name} contains too many items")

    normalized: list[str] = []
    for item in value:
        text = _required_text(item, field_name, MAX_BOUNDARY_ITEM_CHARACTERS)
        if text not in normalized:
            normalized.append(text)
    return tuple(normalized)
