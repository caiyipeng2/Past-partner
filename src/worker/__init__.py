"""External worker runtime for the encrypted durable task queue."""

from __future__ import annotations

from dataclasses import dataclass
import re
import threading
from collections.abc import Callable, Mapping
from typing import Any

from src.services.task_queue import TaskQueue
from src.services.task_worker import TaskHandler, TaskWorker


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class WorkerConfigurationError(ValueError):
    """Stable worker startup error that never includes secrets or paths."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    """Validated process-level settings shared by all worker launch modes."""

    worker_id: str
    lease_seconds: int = 60
    poll_seconds: float = 1.0
    max_tasks: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.worker_id, str) or _IDENTIFIER.fullmatch(self.worker_id) is None:
            raise WorkerConfigurationError("worker_id_invalid", "worker ID is invalid")
        if (
            isinstance(self.lease_seconds, bool)
            or not isinstance(self.lease_seconds, int)
            or not 1 <= self.lease_seconds <= 60 * 60
        ):
            raise WorkerConfigurationError("lease_seconds_invalid", "worker lease duration is invalid")
        if (
            isinstance(self.poll_seconds, bool)
            or not isinstance(self.poll_seconds, (int, float))
            or not 0 < self.poll_seconds <= 300
        ):
            raise WorkerConfigurationError("poll_seconds_invalid", "worker poll interval is invalid")
        if self.max_tasks is not None and (
            isinstance(self.max_tasks, bool)
            or not isinstance(self.max_tasks, int)
            or self.max_tasks < 1
        ):
            raise WorkerConfigurationError("max_tasks_invalid", "worker task limit is invalid")


@dataclass(slots=True)
class WorkerStats:
    """Bounded in-process counters suitable for redacted lifecycle logging."""

    polls: int = 0
    claimed: int = 0
    idle_polls: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "polls": self.polls,
            "claimed": self.claimed,
            "idle_polls": self.idle_polls,
        }


class WorkerRunner:
    """Run a :class:`TaskWorker` with bounded lifecycle controls.

    The runner deliberately contains no task business logic. A deployment owns
    its handler registry, while claim/lease/retry state remains in the shared
    encrypted queue so another process can recover an expired lease.
    """

    def __init__(
        self,
        queue: TaskQueue,
        handlers: Mapping[str, TaskHandler],
        settings: WorkerSettings,
    ) -> None:
        self.settings = settings
        self.worker = TaskWorker(
            queue,
            handlers,
            worker_id=settings.worker_id,
            lease_seconds=settings.lease_seconds,
            poll_seconds=settings.poll_seconds,
        )
        self.stats = WorkerStats()

    def run_once(self, *, now: str | None = None) -> bool:
        self.stats.polls += 1
        claimed = self.worker.run_once(now=now)
        if claimed:
            self.stats.claimed += 1
        else:
            self.stats.idle_polls += 1
        return claimed

    def run_until_idle(self, *, max_tasks: int | None = None) -> int:
        """Process at most the configured limit, stopping at the first idle poll."""

        limit = self.settings.max_tasks if max_tasks is None else max_tasks
        if limit is None:
            raise WorkerConfigurationError(
                "max_tasks_required", "bounded worker runs require a task limit"
            )
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise WorkerConfigurationError("max_tasks_invalid", "worker task limit is invalid")
        processed = 0
        while processed < limit and self.run_once():
            processed += 1
        return processed

    def run_forever(self, stop_event: threading.Event) -> None:
        """Poll until SIGTERM/SIGINT or another owner sets ``stop_event``."""

        while not stop_event.is_set():
            if not self.run_once():
                stop_event.wait(self.settings.poll_seconds)


def test_probe_handlers(mode: str) -> Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]]:
    """Return a non-sensitive deterministic handler only for test mode.

    Production must register real business handlers explicitly in a later R1-04
    slice. Returning no implicit production handler prevents a queued task from
    being reported as successful merely because the worker process is running.
    """

    if mode != "test":
        return {}

    def probe(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"ok": True, "payload_keys": sorted(str(key) for key in payload)[:32]}

    return {"worker.probe": probe}


__all__ = [
    "WorkerConfigurationError",
    "WorkerRunner",
    "WorkerSettings",
    "WorkerStats",
    "test_probe_handlers",
]
