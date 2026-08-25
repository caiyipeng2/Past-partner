"""Privacy-preserving task notification values shared by queue and brokers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .task_queue import parse_timestamp


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class TaskNotificationValidationError(ValueError):
    """Stable validation failure that never includes payload or owner data."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class TaskNotification:
    """The only task data allowed to cross the broker boundary.

    The task ID doubles as the idempotency key. A broker may redeliver this
    notification, but it can never reconstruct the encrypted task payload from it.
    """

    message_id: str
    task_id: str
    task_type: str
    created_at: str

    def __post_init__(self) -> None:
        for field_name, value in (
            ("message_id", self.message_id),
            ("task_id", self.task_id),
            ("task_type", self.task_type),
        ):
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise TaskNotificationValidationError(
                    "notification_invalid", f"{field_name} is invalid"
                )
        if self.message_id != self.task_id:
            raise TaskNotificationValidationError(
                "notification_invalid", "message ID must match task ID"
            )
        if not isinstance(self.created_at, str):
            raise TaskNotificationValidationError("notification_invalid", "timestamp is invalid")
        try:
            parse_timestamp(self.created_at)
        except (TypeError, ValueError) as exc:
            raise TaskNotificationValidationError(
                "notification_invalid", "timestamp is invalid"
            ) from exc

    def to_mapping(self) -> dict[str, Any]:
        """Return the complete broker-safe representation, never task data."""

        return {
            "message_id": self.message_id,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class BrokerDelivery:
    """A broker delivery whose acknowledgement is bound to one consumer."""

    delivery_id: str
    consumer_id: str
    notification: TaskNotification
    attempt: int


__all__ = [
    "BrokerDelivery",
    "TaskNotification",
    "TaskNotificationValidationError",
]
