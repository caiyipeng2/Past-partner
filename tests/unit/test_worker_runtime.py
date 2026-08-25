"""R1-04 external worker runtime contracts."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch

from src.worker import (
    WorkerConfigurationError,
    WorkerRunner,
    WorkerSettings,
    test_probe_handlers,
)


class WorkerSettingsTests(unittest.TestCase):
    def test_accepts_bounded_worker_settings(self) -> None:
        settings = WorkerSettings(
            worker_id="worker-a",
            lease_seconds=30,
            poll_seconds=0.25,
            max_tasks=2,
        )

        self.assertEqual("worker-a", settings.worker_id)
        self.assertEqual(30, settings.lease_seconds)
        self.assertEqual(0.25, settings.poll_seconds)
        self.assertEqual(2, settings.max_tasks)

    def test_rejects_invalid_identifiers_and_limits(self) -> None:
        invalid = (
            {"worker_id": "worker with spaces"},
            {"worker_id": ""},
            {"worker_id": "worker-a", "lease_seconds": 0},
            {"worker_id": "worker-a", "lease_seconds": 3601},
            {"worker_id": "worker-a", "poll_seconds": 0},
            {"worker_id": "worker-a", "poll_seconds": 301.0},
            {"worker_id": "worker-a", "max_tasks": 0},
            {"worker_id": "worker-a", "max_tasks": True},
        )

        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(WorkerConfigurationError):
                    WorkerSettings(**values)


class WorkerRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = Mock()
        self.handlers = {"worker.probe": lambda payload: {"ok": True}}
        self.settings = WorkerSettings(worker_id="worker-a", poll_seconds=0.001)

    @patch("src.worker.TaskWorker")
    def test_run_once_tracks_claimed_and_idle_polls(self, worker_type: Mock) -> None:
        worker = worker_type.return_value
        worker.run_once.side_effect = [True, False]
        runner = WorkerRunner(self.queue, self.handlers, self.settings)

        self.assertTrue(runner.run_once(now="2026-08-25T10:00:00+00:00"))
        self.assertFalse(runner.run_once(now="2026-08-25T10:00:01+00:00"))

        self.assertEqual(
            {"polls": 2, "claimed": 1, "idle_polls": 1},
            runner.stats.to_dict(),
        )

    @patch("src.worker.TaskWorker")
    def test_run_until_idle_honors_max_tasks(self, worker_type: Mock) -> None:
        worker = worker_type.return_value
        worker.run_once.return_value = True
        runner = WorkerRunner(
            self.queue,
            self.handlers,
            WorkerSettings(worker_id="worker-a", max_tasks=2),
        )

        self.assertEqual(2, runner.run_until_idle())
        self.assertEqual(2, runner.stats.claimed)
        self.assertEqual(2, worker.run_once.call_count)

    @patch("src.worker.TaskWorker")
    def test_run_forever_stops_cooperatively(self, worker_type: Mock) -> None:
        worker = worker_type.return_value
        worker.run_once.return_value = False
        stop_event = threading.Event()
        runner = WorkerRunner(
            self.queue,
            self.handlers,
            WorkerSettings(worker_id="worker-a", poll_seconds=0.001),
        )

        def stop_after_wait(_timeout: float) -> None:
            stop_event.set()

        stop_event.wait = stop_after_wait  # type: ignore[method-assign]
        runner.run_forever(stop_event)

        self.assertEqual(1, runner.stats.idle_polls)

    def test_probe_handler_is_test_only_and_does_not_echo_payload(self) -> None:
        self.assertEqual({}, test_probe_handlers("production"))
        result = test_probe_handlers("test")["worker.probe"](
            {"secret": "must-not-be-persisted", "value": 1}
        )

        self.assertEqual({"ok": True, "payload_keys": ["secret", "value"]}, result)
        self.assertNotIn("must-not-be-persisted", result)


if __name__ == "__main__":
    unittest.main()
