"""Stable domain contracts shared by every client and transport."""

from .attachments import AttachmentValidationError, normalize_attachments
from .messages import MessageValidationError, NormalizedMessage
from .personas import Persona, PersonaValidationError, RelationshipType

__all__ = [
    "MessageValidationError",
    "NormalizedMessage",
    "AttachmentValidationError",
    "normalize_attachments",
    "Persona",
    "PersonaValidationError",
    "RelationshipType",
]
