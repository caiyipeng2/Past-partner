"""Stable domain contracts shared by every client and transport."""

from .messages import MessageValidationError, NormalizedMessage
from .personas import Persona, PersonaValidationError, RelationshipType

__all__ = [
    "MessageValidationError",
    "NormalizedMessage",
    "Persona",
    "PersonaValidationError",
    "RelationshipType",
]
