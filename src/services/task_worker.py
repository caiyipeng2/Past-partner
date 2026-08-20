"""Bounded worker runner for the durable task queue."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re
import threading
from typing import Any

from src.domain.task_queue import TaskRecord
from src.services.task_queue import TaskQueue, TaskQueueError


class RetryableTaskError(RuntimeError):
    """A handler failure that should be retried until the task budget is spent."""

    def __init__(self, code: str, message: str = "task is retryable"):
        super().__init__(message)
        self.code = code


TaskHandler = Callable[[Mapping[str, Any]], Mapping[str, Any] | None]


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
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self.queue = queue
        self.handlers = dict(handlers)
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.poll_seconds = poll_seconds

    def run_once(self, *, now: str | None = None) -> bool:
        lease = self.queue.claim(
            self.worker_id,
            now=now,
            lease_seconds=self.lease_seconds,
        )
        if lease is None:
            return False

        handler = self.handlers.get(lease.task_type)
        if handler is None:
            self._fail(lease, "task_handler_unavailable", retryable=False, now=now)
            return True
        try:
            result = handler(lease.payload)
            if result is not None and not isinstance(result, Mapping):
                raise TypeError("task handler result must be an object")
        except RetryableTaskError as exc:
            self._fail(lease, exc.code, retryable=True, now=now)
        except Exception:
            # Handler messages can contain provider responses or local paths. The
            # queue records only this stable code and never the exception text.
            self._fail(lease, "task_failed", retryable=False, now=now)
        else:
            try:
                self.queue.complete(
                    lease.owner_id,
                    lease.id,
                    self.worker_id,
                    result=result,
                    now=now,
                )
            except TaskQueueError:
                # A lease may expire while an external handler is running. The
                # next worker can reclaim it; do not overwrite another worker's
                # state or turn a lease race into a process crash.
                pass
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
    ) -> None:
        # A handler can construct RetryableTaskError from untrusted provider
        # metadata. Normalize malformed codes before they reach the queue so a
        # bad provider response cannot strand a task in a lease forever.
        normalized_code = code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) else "task_retryable"
        try:
            self.queue.fail(
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
            return
