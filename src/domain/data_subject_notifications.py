"""Bounded lifecycle metadata for owner data-subject notifications."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
import re
from typing import Any, Mapping
from uuid import uuid4


class NotificationValidationError(ValueError):
    """Raised when notification metadata or a delivery transition is invalid."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_EVENT_TYPES = frozenset({"export_completed", "deletion_completed"})
_STATES = frozenset({"pending", "delivered", "failed"})
_COUNT_KEYS = frozenset(
    {
        "personas",
        "imports",
        "consents",
        "training_jobs",
        "conversations",
        "style_profiles",
        "long_term_memories",
        "vector_indexes",
        "usage_records",
        "billing_accounts",
        "billing_entries",
        "subscriptions",
        "subscription_events",
        "subscription_bindings",
        "audit_events",
        "task_queue",
        "sessions",
        "provider_side_cleanup_limitations",
    }
)
_MAX_COUNT_ITEMS = 32
_MAX_ATTEMPTS = 32


@dataclass(frozen=True, slots=True)
class DataSubjectNotification:
    """Encrypted owner notification containing no raw content or credentials."""

    id: str
    owner_id: str
    event_type: str
    operation_id: str
    counts: Mapping[str, int]
    occurred_at: str
    status: str = "pending"
    attempts: int = 0
    next_attempt_at: str | None = None
    last_error_code: str | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.id, "id"),
            (self.owner_id, "owner_id"),
            (self.operation_id, "operation_id"),
        ):
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise NotificationValidationError("notification_invalid", f"{field_name} is invalid")
        if not isinstance(self.event_type, str) or self.event_type not in _EVENT_TYPES:
            raise NotificationValidationError("notification_invalid", "event type is invalid")
        if not isinstance(self.status, str) or self.status not in _STATES:
            raise NotificationValidationError("notification_invalid", "notification state is invalid")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int) or not 0 <= self.attempts <= _MAX_ATTEMPTS:
            raise NotificationValidationError("notification_invalid", "notification attempts are invalid")
        _canonical_timestamp(self.occurred_at, "occurred_at")
        if self.next_attempt_at is not None:
            _canonical_timestamp(self.next_attempt_at, "next_attempt_at")
        if self.last_error_code is not None and (
            not isinstance(self.last_error_code, str)
            or _FAILURE_CODE.fullmatch(self.last_error_code) is None
        ):
            raise NotificationValidationError("notification_invalid", "notification error code is invalid")
        if self.status == "delivered" and (self.next_attempt_at is not None or self.last_error_code is not None):
            raise NotificationValidationError("notification_invalid", "delivered notification has retry metadata")
        if self.status == "pending" and self.last_error_code is not None:
            raise NotificationValidationError("notification_invalid", "pending notification has an error code")
        if not isinstance(self.counts, Mapping) or len(self.counts) > _MAX_COUNT_ITEMS:
            raise NotificationValidationError("notification_invalid", "notification counts are invalid")
        encoded_size = 2
        for key, value in self.counts.items():
            if not isinstance(key, str) or key not in _COUNT_KEYS:
                raise NotificationValidationError("notification_invalid", "notification count key is invalid")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 1_000_000_000:
                raise NotificationValidationError("notification_invalid", "notification count is invalid")
            encoded_size += len(key) + 16
        if encoded_size > 4096:
            raise NotificationValidationError("notification_invalid", "notification counts are too large")

    @classmethod
    def create(
        cls,
        *,
        owner_id: object,
        event_type: object,
        operation_id: object,
        counts: Mapping[str, int] | None = None,
        occurred_at: object,
        notification_id: object | None = None,
    ) -> "DataSubjectNotification":
        return cls(
            id=str(uuid4()) if notification_id is None else str(notification_id),
            owner_id=owner_id,  # type: ignore[arg-type]
            event_type=event_type,  # type: ignore[arg-type]
            operation_id=operation_id,  # type: ignore[arg-type]
            counts=dict(counts or {}),
            occurred_at=occurred_at,  # type: ignore[arg-type]
        )

    @classmethod
    def from_storage_dict(cls, value: Mapping[str, Any]) -> "DataSubjectNotification":
        if not isinstance(value, Mapping):
            raise NotificationValidationError("notification_invalid", "notification must be an object")
        try:
            counts = value["counts"]
            if not isinstance(counts, Mapping):
                raise TypeError("counts must be an object")
            return cls(
                id=value["id"],
                owner_id=value["owner_id"],
                event_type=value["event_type"],
                operation_id=value["operation_id"],
                counts=dict(counts),
                occurred_at=value["occurred_at"],
                status=value.get("status", "pending"),
                attempts=value.get("attempts", 0),
                next_attempt_at=value.get("next_attempt_at"),
                last_error_code=value.get("last_error_code"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise NotificationValidationError("notification_invalid", "notification is invalid") from exc

    def mark_failed(self, error_code: str, next_attempt_at: str) -> "DataSubjectNotification":
        if self.status == "delivered":
            raise NotificationValidationError("notification_closed", "delivered notification cannot fail")
        if self.attempts >= _MAX_ATTEMPTS:
            raise NotificationValidationError("notification_retry_limit", "notification retry limit reached")
        if not isinstance(error_code, str) or _FAILURE_CODE.fullmatch(error_code) is None:
            raise NotificationValidationError("notification_invalid", "notification error code is invalid")
        return replace(
            self,
            status="failed",
            attempts=self.attempts + 1,
            next_attempt_at=_canonical_timestamp(next_attempt_at, "next_attempt_at"),
            last_error_code=error_code,
        )

    def retry(self, now: str) -> "DataSubjectNotification":
        if self.status != "failed":
            raise NotificationValidationError("notification_not_retryable", "notification is not failed")
        return replace(
            self,
            status="pending",
            next_attempt_at=_canonical_timestamp(now, "next_attempt_at"),
            last_error_code=None,
        )

    def mark_delivered(self, now: str) -> "DataSubjectNotification":
        if self.status == "delivered":
            return self
        if self.attempts >= _MAX_ATTEMPTS:
            raise NotificationValidationError("notification_retry_limit", "notification retry limit reached")
        return replace(
            self,
            status="delivered",
            attempts=self.attempts + 1,
            next_attempt_at=None,
            last_error_code=None,
            occurred_at=self.occurred_at,
        )

    def to_storage_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "event_type": self.event_type,
            "operation_id": self.operation_id,
            "counts": dict(self.counts),
            "occurred_at": self.occurred_at,
            "status": self.status,
            "attempts": self.attempts,
            "next_attempt_at": self.next_attempt_at,
            "last_error_code": self.last_error_code,
        }

    def to_public_dict(self) -> dict[str, Any]:
        value = self.to_storage_dict()
        value.pop("owner_id", None)
        return value


def _canonical_timestamp(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise NotificationValidationError("notification_invalid", f"{field_name} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise NotificationValidationError("notification_invalid", f"{field_name} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NotificationValidationError("notification_invalid", f"{field_name} is invalid")
    return parsed.astimezone(UTC).isoformat()


__all__ = ["DataSubjectNotification", "NotificationValidationError"]
