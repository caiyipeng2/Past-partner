"""Durable, redacted worker observations and deterministic internal alerts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from typing import Sequence
from uuid import uuid4

from src.domain.task_queue import parse_timestamp
from src.domain.worker_observability import (
    WorkerAlert,
    WorkerAlertSeverity,
    WorkerObservation,
    WorkerObservationOutcome,
    WorkerObservationValidationError,
)
from src.services.metadata_store import MetadataStoreError, require_metadata_store


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FAILURE_OUTCOMES = frozenset(
    {
        WorkerObservationOutcome.RETRYABLE_FAILURE,
        WorkerObservationOutcome.TERMINAL_FAILURE,
        WorkerObservationOutcome.LEASE_LOST,
    }
)


class WorkerObservabilityError(RuntimeError):
    """Stable operational error without driver details or observation data."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class WorkerObservability:
    """Persist lifecycle values and evaluate bounded internal alert rules.

    Observations are intentionally append-only at the API boundary, then
    pruned in the same transaction by age and per-worker count. This keeps a
    long-running worker from turning operational telemetry into an unbounded
    metadata store while allowing multiple processes to share one backend.
    """

    def __init__(
        self,
        metadata_store: object,
        *,
        retention_seconds: int = 7 * 24 * 60 * 60,
        max_observations_per_worker: int = 10_000,
    ) -> None:
        if (
            isinstance(retention_seconds, bool)
            or not isinstance(retention_seconds, int)
            or not 60 <= retention_seconds <= 31 * 24 * 60 * 60
        ):
            raise ValueError("retention_seconds must be between 60 and 2678400")
        if (
            isinstance(max_observations_per_worker, bool)
            or not isinstance(max_observations_per_worker, int)
            or not 1 <= max_observations_per_worker <= 100_000
        ):
            raise ValueError("max_observations_per_worker must be between 1 and 100000")
        self.metadata_store = require_metadata_store(metadata_store)
        self.retention_seconds = retention_seconds
        self.max_observations_per_worker = max_observations_per_worker

    def record(self, observation: WorkerObservation, *, now: str | None = None) -> None:
        """Persist one observation and prune this worker's old rows.

        The queue result is authoritative. Callers should treat a telemetry
        write failure as auxiliary and keep processing the task; the service
        exposes only a stable error for diagnostics.
        """

        if not isinstance(observation, WorkerObservation):
            raise WorkerObservabilityError("observation_invalid", "worker observation is invalid")
        current = _timestamp(now or observation.observed_at, "observation_now")
        # Store one canonical UTC representation. Retention and cursor queries
        # use SQLite/PostgreSQL text ordering, so preserving an arbitrary
        # offset would make equivalent instants sort and expire inconsistently.
        observed_at = _timestamp(observation.observed_at, "observed_at").isoformat()
        cutoff = (current - timedelta(seconds=self.retention_seconds)).isoformat()
        try:
            with self.metadata_store.transaction(
                immediate=self.metadata_store.backend_name == "sqlite"
            ) as connection:
                connection.execute(
                    "DELETE FROM worker_observations WHERE observed_at < ?",
                    (cutoff,),
                )
                connection.execute(
                    """
                    INSERT INTO worker_observations
                        (id, worker_id, task_type, outcome, observed_at, duration_ms, failure_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid4().hex,
                        observation.worker_id,
                        observation.task_type,
                        observation.outcome.value,
                        observed_at,
                        observation.duration_ms,
                        observation.failure_code,
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM worker_observations
                    WHERE worker_id = ?
                      AND id NOT IN (
                          SELECT id FROM worker_observations
                          WHERE worker_id = ?
                          ORDER BY observed_at DESC, id DESC
                          LIMIT ?
                      )
                    """,
                    (
                        observation.worker_id,
                        observation.worker_id,
                        self.max_observations_per_worker,
                    ),
                )
        except MetadataStoreError as exc:
            raise WorkerObservabilityError(
                "observation_unavailable", "worker observation could not be persisted"
            ) from exc

    def recent(
        self,
        *,
        since: str | None = None,
        worker_id: str | None = None,
        limit: int | None = None,
    ) -> tuple[WorkerObservation, ...]:
        """Read a bounded newest-first view for internal aggregation only."""

        if worker_id is not None:
            _identifier(worker_id, "worker_id")
        if limit is None:
            limit = min(100_000, self.max_observations_per_worker * 100)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100_000:
            raise ValueError("observation limit is invalid")
        parameters: list[object] = []
        predicates: list[str] = []
        if since is not None:
            start = _timestamp(since, "since")
            predicates.append("observed_at >= ?")
            parameters.append(start.isoformat())
        if worker_id is not None:
            predicates.append("worker_id = ?")
            parameters.append(worker_id)
        where = " WHERE " + " AND ".join(predicates) if predicates else ""
        parameters.append(limit)
        try:
            with self.metadata_store.transaction() as connection:
                rows = connection.execute(
                    "SELECT worker_id, task_type, outcome, observed_at, duration_ms, failure_code "
                    "FROM worker_observations"
                    f"{where} ORDER BY observed_at DESC, id DESC LIMIT ?",
                    tuple(parameters),
                ).fetchall()
        except MetadataStoreError as exc:
            raise WorkerObservabilityError(
                "observation_unavailable", "worker observations could not be read"
            ) from exc
        observations: list[WorkerObservation] = []
        try:
            for row in rows:
                observations.append(
                    WorkerObservation(
                        worker_id=str(row[0]),
                        task_type=str(row[1]),
                        outcome=WorkerObservationOutcome(str(row[2])),
                        observed_at=str(row[3]),
                        duration_ms=int(row[4]),
                        failure_code=None if row[5] is None else str(row[5]),
                    )
                )
        except (TypeError, ValueError, WorkerObservationValidationError) as exc:
            raise WorkerObservabilityError(
                "observation_corrupt", "worker observation is invalid"
            ) from exc
        return tuple(observations)

    def evaluate_alerts(
        self,
        *,
        worker_ids: Sequence[str] = (),
        now: str | None = None,
        window_seconds: int = 300,
        heartbeat_timeout_seconds: int = 120,
        min_samples: int = 3,
        failure_rate: float = 0.5,
    ) -> tuple[WorkerAlert, ...]:
        """Return deterministic stale-worker and high-failure alerts.

        Alerts are computed from persisted, redacted rows and are not exposed
        through the owner API in this slice. A deployment can later export the
        returned values to its monitoring system without changing worker code.
        """

        current = _timestamp(now or _utc_now(), "alert_now")
        _bounded_integer(window_seconds, 1, 24 * 60 * 60, "window_seconds")
        _bounded_integer(heartbeat_timeout_seconds, 1, window_seconds, "heartbeat_timeout_seconds")
        _bounded_integer(min_samples, 1, 100_000, "min_samples")
        if isinstance(failure_rate, bool) or not isinstance(failure_rate, (int, float)):
            raise ValueError("failure_rate is invalid")
        if not 0.0 <= float(failure_rate) <= 1.0:
            raise ValueError("failure_rate is invalid")
        normalized_workers = tuple(sorted(set(worker_ids)))
        for worker_id in normalized_workers:
            _identifier(worker_id, "worker_id")
        window_start = current - timedelta(seconds=window_seconds)
        observations = self.recent(since=window_start.isoformat())
        alerts: list[WorkerAlert] = []
        grouped: dict[tuple[str, str], list[WorkerObservation]] = {}
        latest_by_worker: dict[str, WorkerObservation] = {}
        for observation in observations:
            latest = latest_by_worker.get(observation.worker_id)
            if latest is None or parse_timestamp(observation.observed_at) > parse_timestamp(latest.observed_at):
                latest_by_worker[observation.worker_id] = observation
            if observation.outcome is WorkerObservationOutcome.IDLE:
                continue
            grouped.setdefault((observation.worker_id, observation.task_type), []).append(observation)

        for (worker_id, task_type), values in sorted(grouped.items()):
            sample_count = len(values)
            failure_count = sum(value.outcome in _FAILURE_OUTCOMES for value in values)
            if sample_count < min_samples or failure_count / sample_count < float(failure_rate):
                continue
            severity = (
                WorkerAlertSeverity.CRITICAL
                if failure_count / sample_count >= 0.8
                else WorkerAlertSeverity.WARNING
            )
            alerts.append(
                WorkerAlert(
                    code="worker_failure_rate_high",
                    severity=severity,
                    worker_id=worker_id,
                    task_type=task_type,
                    window_start=window_start.isoformat(),
                    observed_at=current.isoformat(),
                    sample_count=sample_count,
                    failure_count=failure_count,
                )
            )

        heartbeat_cutoff = current - timedelta(seconds=heartbeat_timeout_seconds)
        for worker_id in normalized_workers:
            latest = latest_by_worker.get(worker_id)
            if latest is not None and parse_timestamp(latest.observed_at) >= heartbeat_cutoff:
                continue
            alerts.append(
                WorkerAlert(
                    code="worker_no_heartbeat",
                    severity=WorkerAlertSeverity.CRITICAL,
                    worker_id=worker_id,
                    task_type="worker.idle",
                    window_start=window_start.isoformat(),
                    observed_at=current.isoformat(),
                    sample_count=0,
                    failure_count=0,
                )
            )
        alerts.sort(key=lambda value: (value.code, value.worker_id, value.task_type))
        return tuple(alerts)


def _timestamp(value: str, field_name: str) -> datetime:
    try:
        return parse_timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc


def _bounded_integer(value: object, minimum: int, maximum: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{field_name} is invalid")


def _identifier(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} is invalid")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = ["WorkerObservability", "WorkerObservabilityError"]
