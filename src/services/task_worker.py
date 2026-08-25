"""Bounded worker runner for the durable task queue."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re
import threading
import time
from typing import Any

from src.domain.task_queue import TaskRecord, TaskState, utc_now
from src.domain.worker_observability import WorkerObservation, WorkerObservationOutcome
from src.services.task_queue import TaskQueue, TaskQueueError


class RetryableTaskError(RuntimeError):
    """A handler failure that should be retried until the task budget is spent."""

    def __init__(self, code: str, message: str = "task is retryable"):
        super().__init__(message)
        self.code = code


TaskHandler = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]
ObservationSink = Callable[[WorkerObservation], None]


class TaskWorker:
    """Run registered task handlers one lease at a time.

    This class intentionally does not create a thread by itself. Deployments can
    run several processes, containers, or scheduled invocations against the same
    queue; ``run_forever`` is provided only as a small process-local loop.
    """

    def __init__(
        self,
        queue: TaskQueue,
        handlers: Mapping[str, TaskHandler],
        *,
        worker_id: str,
        lease_seconds: int = 60,
        poll_seconds: float = 1.0,
        observation_sink: ObservationSink | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.queue = queue
        self.handlers = dict(handlers)
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds
        self._observation_sink = observation_sink
        self._clock = clock

    def run_once(self, *, now: str | None = None) -> bool:
        observed_at = utc_now() if now is None else now
        started = self._clock()
        lease = self.queue.claim(
            self.worker_id,
            now=observed_at,
            lease_seconds=self.lease_seconds,
        )
        if lease is None:
            self._emit_observation(
                task_type="worker.idle",
                outcome=WorkerObservationOutcome.IDLE,
                observed_at=observed_at,
                started=started,
            )
            return False

        handler = self.handlers.get(lease.task_type)
        if handler is None:
            failed = self._fail(lease, "task_handler_unavailable", retryable=False, now=observed_at)
            self._emit_failure_observation(lease.task_type, failed, observed_at, started, "task_handler_unavailable")
            return True
        try:
            result = handler(lease.payload)
            if result is not None and not isinstance(result, Mapping):
                raise TypeError("task handler result must be an object")
        except RetryableTaskError as exc:
            failed = self._fail(lease, exc.code, retryable=True, now=observed_at)
            normalized_code = exc.code if isinstance(exc.code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", exc.code) else "task_retryable"
            self._emit_failure_observation(lease.task_type, failed, observed_at, started, normalized_code)
        except Exception:
            # Handler messages can contain provider responses or local paths. The
            # queue records only this stable code and never the exception text.
            failed = self._fail(lease, "task_failed", retryable=False, now=observed_at)
            self._emit_failure_observation(lease.task_type, failed, observed_at, started, "task_failed")
        else:
            try:
                self.queue.complete(
                    lease.owner_id,
                    lease.id,
                    self.worker_id,
                    result=result,
                    now=observed_at,
                )
            except TaskQueueError:
                # A lease may expire while an external handler is running. The
                # next worker can reclaim it; do not overwrite another worker's
                # state or turn a lease race into a process crash.
                self._emit_observation(
                    task_type=lease.task_type,
                    outcome=WorkerObservationOutcome.LEASE_LOST,
                    failure_code="lease_lost",
                    observed_at=observed_at,
                    started=started,
                )
            else:
                self._emit_observation(
                    task_type=lease.task_type,
                    outcome=WorkerObservationOutcome.SUCCEEDED,
                    observed_at=observed_at,
                    started=started,
                )
        return True

    def run_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(self.poll_seconds)

    def _fail(
        self,
        lease: TaskRecord,
        code: str,
        *,
        retryable: bool,
        now: str | None,
    ) -> TaskRecord | None:
        # A handler can construct RetryableTaskError from untrusted provider
        # metadata. Normalize malformed codes before they reach the queue so a
        # bad provider response cannot strand a task in a lease forever.
        normalized_code = code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) else "task_retryable"
        try:
            return self.queue.fail(
                lease.owner_id,
                lease.id,
                self.worker_id,
                normalized_code,
                retryable=retryable,
                now=now,
            )
        except TaskQueueError:
            # A lost lease is recoverable by another worker and must not expose
            # handler/provider details through an exception path.
            return None

    def _emit_failure_observation(
        self,
        task_type: str,
        failed: TaskRecord | None,
        observed_at: str,
        started: float,
        fallback_code: str,
    ) -> None:
        if failed is None:
            self._emit_observation(
                task_type=task_type,
                outcome=WorkerObservationOutcome.LEASE_LOST,
                failure_code="lease_lost",
                observed_at=observed_at,
                started=started,
            )
            return
        outcome = (
            WorkerObservationOutcome.RETRYABLE_FAILURE
            if failed.state is TaskState.QUEUED
            else WorkerObservationOutcome.TERMINAL_FAILURE
        )
        self._emit_observation(
            task_type=task_type,
            outcome=outcome,
            failure_code=failed.failure_code or fallback_code,
            observed_at=observed_at,
            started=started,
        )

    def _emit_observation(
        self,
        *,
        task_type: str,
        outcome: WorkerObservationOutcome,
        observed_at: str,
        started: float,
        failure_code: str | None = None,
    ) -> None:
        if self._observation_sink is None:
            return
        try:
            duration_ms = max(0, min(3_600_000, int((self._clock() - started) * 1000)))
            observation = WorkerObservation(
                worker_id=self.worker_id,
                task_type=task_type,
                outcome=outcome,
                observed_at=observed_at,
                duration_ms=duration_ms,
                failure_code=failure_code,
            )
            self._observation_sink(observation)
        except Exception:
            # Telemetry is auxiliary. A broken observer must never turn a
            # completed, retried, or reclaimed queue operation into a failure.
            return
