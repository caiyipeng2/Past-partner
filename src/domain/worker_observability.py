"""Redacted worker lifecycle values shared by persistence and alerting."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any

from src.domain.task_queue import parse_timestamp


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_ALERT_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_MAX_DURATION_MS = 3_600_000


class WorkerObservationValidationError(ValueError):
    """Stable validation failure for a value that would cross the boundary."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class WorkerObservationOutcome(str, Enum):
    IDLE = "idle"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"
    LEASE_LOST = "lease_lost"


class WorkerAlertSeverity(str, Enum):
    WARNING = "warning"
    CRITICAL = "critical"


_FAILURE_OUTCOMES = frozenset(
    {
        WorkerObservationOutcome.RETRYABLE_FAILURE,
        WorkerObservationOutcome.TERMINAL_FAILURE,
        WorkerObservationOutcome.LEASE_LOST,
    }
)


@dataclass(frozen=True, slots=True)
class WorkerObservation:
    """One sanitized worker poll result.

    The value intentionally has no owner, task ID, payload, exception text,
    provider metadata, or local path. It is safe to persist in a shared
    operational table and to aggregate across worker processes.
    """

    worker_id: str
    task_type: str
    outcome: WorkerObservationOutcome
    observed_at: str
    duration_ms: int
    failure_code: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.worker_id, "worker_id")
        _identifier(self.task_type, "task_type")
        if not isinstance(self.outcome, WorkerObservationOutcome):
            raise WorkerObservationValidationError("observation_invalid", "worker outcome is invalid")
        if not isinstance(self.observed_at, str):
            raise WorkerObservationValidationError("observation_invalid", "observation timestamp is invalid")
        try:
            parse_timestamp(self.observed_at)
        except (TypeError, ValueError) as exc:
            raise WorkerObservationValidationError(
                "observation_invalid", "observation timestamp is invalid"
            ) from exc
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or not 0 <= self.duration_ms <= _MAX_DURATION_MS
        ):
            raise WorkerObservationValidationError("observation_invalid", "observation duration is invalid")
        if self.failure_code is not None and (
            not isinstance(self.failure_code, str) or _FAILURE_CODE.fullmatch(self.failure_code) is None
        ):
            raise WorkerObservationValidationError("observation_invalid", "observation failure code is invalid")
        if self.outcome in _FAILURE_OUTCOMES and self.failure_code is None:
            raise WorkerObservationValidationError(
                "observation_invalid", "failure outcomes require a failure code"
            )
        if self.outcome not in _FAILURE_OUTCOMES and self.failure_code is not None:
            raise WorkerObservationValidationError(
                "observation_invalid", "non-failure outcomes cannot carry a failure code"
            )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "task_type": self.task_type,
            "outcome": self.outcome.value,
            "observed_at": self.observed_at,
            "duration_ms": self.duration_ms,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class WorkerAlert:
    """A bounded internal alert result derived from recent observations."""

    code: str
    severity: WorkerAlertSeverity
    worker_id: str
    task_type: str
    window_start: str
    observed_at: str
    sample_count: int
    failure_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or _ALERT_CODE.fullmatch(self.code) is None:
            raise WorkerObservationValidationError("alert_invalid", "alert code is invalid")
        if not isinstance(self.severity, WorkerAlertSeverity):
            raise WorkerObservationValidationError("alert_invalid", "alert severity is invalid")
        _identifier(self.worker_id, "worker_id")
        _identifier(self.task_type, "task_type")
        for value, field_name in (
            (self.window_start, "window_start"),
            (self.observed_at, "observed_at"),
        ):
            if not isinstance(value, str):
                raise WorkerObservationValidationError("alert_invalid", f"{field_name} is invalid")
            try:
                parse_timestamp(value)
            except (TypeError, ValueError) as exc:
                raise WorkerObservationValidationError("alert_invalid", f"{field_name} is invalid") from exc
        if (
            isinstance(self.sample_count, bool)
            or not isinstance(self.sample_count, int)
            or self.sample_count < 0
            or self.sample_count > 100_000
        ):
            raise WorkerObservationValidationError("alert_invalid", "alert sample count is invalid")
        if (
            isinstance(self.failure_count, bool)
            or not isinstance(self.failure_count, int)
            or self.failure_count < 0
            or self.failure_count > self.sample_count
        ):
            raise WorkerObservationValidationError("alert_invalid", "alert failure count is invalid")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "worker_id": self.worker_id,
            "task_type": self.task_type,
            "window_start": self.window_start,
            "observed_at": self.observed_at,
            "sample_count": self.sample_count,
            "failure_count": self.failure_count,
        }


def _identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise WorkerObservationValidationError("observation_invalid", f"{field_name} is invalid")


__all__ = [
    "WorkerAlert",
    "WorkerAlertSeverity",
    "WorkerObservation",
    "WorkerObservationOutcome",
    "WorkerObservationValidationError",
]
