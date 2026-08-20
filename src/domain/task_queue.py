"""Validated, redacted task lifecycle values for distributed workers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import re
from typing import Any, Mapping


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TASK_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
MAX_TASK_PAYLOAD_BYTES = 16 * 1024
MAX_TASK_ATTEMPTS = 20


class TaskState(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskValidationError(ValueError):
    """Raised when a task value would violate the durable queue contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: str
    owner_id: str
    task_type: str
    state: TaskState
    attempts: int
    max_attempts: int
    available_at: str
    created_at: str
    updated_at: str
    payload: Mapping[str, Any]
    result: Mapping[str, Any] | None = None
    lease_owner: str | None = None
    leased_until: str | None = None
    failure_code: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        _identifier(self.id, "task_id")
        _identifier(self.owner_id, "owner_id")
        if not isinstance(self.task_type, str) or not _TASK_TYPE.fullmatch(self.task_type):
            raise TaskValidationError("task_invalid", "task type is invalid")
        if not isinstance(self.state, TaskState):
            raise TaskValidationError("task_invalid", "task state is invalid")
        if isinstance(self.attempts, bool) or not isinstance(self.attempts, int) or self.attempts < 0:
            raise TaskValidationError("task_invalid", "task attempts are invalid")
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= MAX_TASK_ATTEMPTS
        ):
            raise TaskValidationError("task_invalid", "task retry limit is invalid")
        if self.attempts > self.max_attempts:
            raise TaskValidationError("task_invalid", "task attempts exceed retry limit")
        for value, field_name in (
            (self.available_at, "available_at"),
            (self.created_at, "created_at"),
            (self.updated_at, "updated_at"),
        ):
            _timestamp(value, field_name)
        if self.leased_until is not None:
            _timestamp(self.leased_until, "leased_until")
        if self.lease_owner is not None:
            _identifier(self.lease_owner, "lease_owner")
        _mapping(self.payload, "task_payload")
        if self.result is not None:
            _mapping(self.result, "task_result")
        if self.failure_code is not None and not _FAILURE_CODE.fullmatch(self.failure_code):
            raise TaskValidationError("task_invalid", "task failure code is invalid")
        if not isinstance(self.retryable, bool):
            raise TaskValidationError("task_invalid", "task retryability is invalid")
        if self.state is TaskState.LEASED and (self.lease_owner is None or self.leased_until is None):
            raise TaskValidationError("task_invalid", "leased task must have an active lease")
        if self.state is not TaskState.LEASED and (self.lease_owner is not None or self.leased_until is not None):
            raise TaskValidationError("task_invalid", "closed or queued task cannot keep a lease")
        if self.state is TaskState.FAILED and self.failure_code is None:
            raise TaskValidationError("task_invalid", "failed task must have a failure code")


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise TaskValidationError("task_invalid", f"{field_name} is invalid")
    return value


def _timestamp(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskValidationError("task_invalid", f"{field_name} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TaskValidationError("task_invalid", f"{field_name} is invalid") from exc
    if parsed.tzinfo is None:
        raise TaskValidationError("task_invalid", f"{field_name} must include a timezone")
    return value


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise TaskValidationError("task_invalid", f"{field_name} must be an object")
    return value


def utc_now() -> str:
    """Return one canonical timestamp format for queue comparisons."""

    return datetime.now(UTC).isoformat()


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(UTC)
