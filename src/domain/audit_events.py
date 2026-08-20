"""Validated, redacted business audit events.

Audit events intentionally have a smaller shape than application records.  The
allow-list prevents callers from accidentally treating the audit trail as a
second copy of chat content, credentials, or local storage paths.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
import math
import re
from types import MappingProxyType
from typing import Any


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_METADATA_KEYS = frozenset(
    {"deleted_children", "provider_id", "model_id", "scope", "reason_code"}
)
_SENSITIVE_MARKERS = frozenset(
    {
        "token",
        "secret",
        "password",
        "credential",
        "content",
        "message",
        "body",
        "path",
        "file",
        "key",
        "authorization",
    }
)


class AuditEventValidationError(ValueError):
    """Stable validation failure without echoing rejected values."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class AuditAction(str, Enum):
    PERSONA_DELETED = "persona_deleted"
    IMPORT_DELETED = "import_deleted"
    CONSENT_REVOKED = "consent_revoked"
    CONSENT_AUTHORIZED = "consent_authorized"
    TRAINING_CANCELLED = "training_cancelled"


class AuditOutcome(str, Enum):
    SUCCESS = "success"


class AuditEvent:
    """Immutable metadata for one completed owner-scoped operation."""

    __slots__ = (
        "id",
        "owner_id",
        "action",
        "outcome",
        "resource_type",
        "resource_id",
        "occurred_at",
        "metadata",
        "__weakref__",
    )

    def __init__(
        self,
        *,
        id: object,
        owner_id: object,
        action: object,
        outcome: object,
        resource_type: object,
        resource_id: object,
        occurred_at: object,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        self.id = _identifier(id, "id")
        self.owner_id = _identifier(owner_id, "owner_id")
        self.action = _enum(action, AuditAction, "action")
        self.outcome = _enum(outcome, AuditOutcome, "outcome")
        self.resource_type = _identifier(resource_type, "resource_type")
        self.resource_id = _identifier(resource_id, "resource_id")
        self.occurred_at = _timestamp(occurred_at)
        self.metadata = _metadata(metadata)

    def __setattr__(self, name: str, value: object) -> None:
        if hasattr(self, name):
            raise AttributeError("AuditEvent is immutable")
        object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "action": self.action.value,
            "outcome": self.outcome.value,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "occurred_at": self.occurred_at,
            "metadata": dict(self.metadata),
        }

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AuditEvent):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __repr__(self) -> str:
        return f"AuditEvent(id={self.id!r}, action={self.action.value!r}, resource_id={self.resource_id!r})"


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise AuditEventValidationError(
            f"invalid_{field_name}", f"{field_name} is invalid"
        )
    return value


def _enum(value: object, enum_type: type[Enum], field_name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError:
            pass
    raise AuditEventValidationError(f"invalid_{field_name}", f"{field_name} is invalid")


def _timestamp(value: object) -> str:
    if not isinstance(value, (str, datetime)):
        raise AuditEventValidationError("invalid_occurred_at", "occurred_at is invalid")
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise AuditEventValidationError("invalid_occurred_at", "occurred_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AuditEventValidationError("invalid_occurred_at", "occurred_at must include a timezone")
    return parsed.astimezone(UTC).isoformat()


def _metadata(value: Mapping[str, object] | None) -> MappingProxyType:
    if value is None:
        return MappingProxyType({})
    if not isinstance(value, Mapping):
        raise AuditEventValidationError("invalid_metadata", "metadata is invalid")
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or any(
            marker in key.casefold().replace("-", "_").split("_") for marker in _SENSITIVE_MARKERS
        ):
            raise AuditEventValidationError("sensitive_metadata", "metadata is not permitted")
        if key not in _METADATA_KEYS:
            raise AuditEventValidationError("invalid_metadata", "metadata is not permitted")
        if isinstance(item, bool) or item is None:
            normalized[key] = item
        elif isinstance(item, int) and not isinstance(item, bool) and abs(item) <= 1_000_000_000:
            normalized[key] = item
        elif isinstance(item, float) and math.isfinite(item):
            normalized[key] = item
        elif isinstance(item, str) and len(item) <= 256 and "\x00" not in item:
            normalized[key] = item
        else:
            raise AuditEventValidationError("invalid_metadata", "metadata is not permitted")
    return MappingProxyType(normalized)
