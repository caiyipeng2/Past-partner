"""P4-06 worker dispatch, retry, and redacted failure behavior."""

from __future__ import annotations

import base64
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.task_queue import TaskState
from src.domain.worker_observability import WorkerObservationOutcome
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.local_auth import LocalAuthService
from src.services.master_key import (
    EnvironmentMasterKeyProvider,
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
)
from src.services.storage import StorageLayout
from src.services.task_queue import TaskQueue
from src.services.task_worker import RetryableTaskError, TaskWorker


class TaskWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"w" * MASTER_KEY_BYTES).decode("ascii")
        encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        auth = LocalAuthService(layout.database_path(), encryption, mode="test")
        self.owner_id = auth.owner_id
        self.queue = TaskQueue(auth.metadata_store, encryption)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_run_once_dispatches_registered_handler_and_persists_result(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "echo",
            {"value": "hello"},
            now="2026-08-20T10:00:00+00:00",
        )
        worker = TaskWorker(
            self.queue,
            {"echo": lambda payload: {"echo": payload["value"]}},
            worker_id="worker-a",
        )

        self.assertTrue(worker.run_once(now="2026-08-20T10:00:01+00:00"))
        completed = self.queue.get(self.owner_id, task.id)

        self.assertEqual(TaskState.SUCCEEDED, completed.state)
        self.assertEqual({"echo": "hello"}, completed.result)

    def test_retryable_handler_error_requeues_without_exposing_exception_text(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "retry",
            {},
            max_attempts=2,
            now="2026-08-20T10:00:00+00:00",
        )

        def handler(_payload: object) -> object:
            raise RetryableTaskError("provider_timeout", "secret provider response")

        worker = TaskWorker(self.queue, {"retry": handler}, worker_id="worker-a")
        self.assertTrue(worker.run_once(now="2026-08-20T10:00:01+00:00"))
        retried = self.queue.get(self.owner_id, task.id)

        self.assertEqual(TaskState.QUEUED, retried.state)
        self.assertEqual("provider_timeout", retried.failure_code)
        self.assertNotIn("secret", repr(retried))

    def test_malformed_retry_code_is_normalized_and_task_remains_retryable(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "retry-invalid-code",
            {},
            max_attempts=2,
            now="2026-08-20T10:00:00+00:00",
        )

        def handler(_payload: object) -> object:
            raise RetryableTaskError("Provider timeout", "untrusted provider detail")

        worker = TaskWorker(self.queue, {"retry-invalid-code": handler}, worker_id="worker-a")
        self.assertTrue(worker.run_once(now="2026-08-20T10:00:01+00:00"))
        retried = self.queue.get(self.owner_id, task.id)
        self.assertEqual(TaskState.QUEUED, retried.state)
        self.assertEqual("task_retryable", retried.failure_code)

    def test_unknown_handler_and_unexpected_error_are_terminal_redacted_failures(self) -> None:
        unknown = self.queue.enqueue(self.owner_id, "missing", {}, now="2026-08-20T10:00:00+00:00")
        broken = self.queue.enqueue(self.owner_id, "broken", {}, now="2026-08-20T10:00:00+00:00")

        def handler(_payload: object) -> object:
            raise RuntimeError("provider-key=do-not-store")

        worker = TaskWorker(
            self.queue,
            {"broken": handler},
            worker_id="worker-a",
        )
        self.assertTrue(worker.run_once(now="2026-08-20T10:00:01+00:00"))
        self.assertTrue(worker.run_once(now="2026-08-20T10:00:02+00:00"))

        unknown_record = self.queue.get(self.owner_id, unknown.id)
        broken_record = self.queue.get(self.owner_id, broken.id)
        self.assertEqual("task_handler_unavailable", unknown_record.failure_code)
        self.assertEqual("task_failed", broken_record.failure_code)
        self.assertNotIn("provider-key", repr(broken_record))
        self.assertFalse(worker.run_once(now="2026-08-20T10:00:03+00:00"))

    def test_emits_sanitized_success_and_idle_observations(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "echo",
            {"secret": "must-not-leak"},
            now="2026-08-20T10:00:00+00:00",
        )
        observations = []
        worker = TaskWorker(
            self.queue,
            {"echo": lambda _payload: {"ok": True}},
            worker_id="worker-a",
            observation_sink=observations.append,
        )

        self.assertTrue(worker.run_once(now="2026-08-20T10:00:01+00:00"))
        self.assertFalse(worker.run_once(now="2026-08-20T10:00:02+00:00"))

        self.assertEqual(
            [WorkerObservationOutcome.SUCCEEDED, WorkerObservationOutcome.IDLE],
            [observation.outcome for observation in observations],
        )
        self.assertEqual("echo", observations[0].task_type)
        self.assertEqual("worker.idle", observations[1].task_type)
        self.assertNotIn("must-not-leak", repr(observations))
        self.assertNotIn(task.owner_id, repr(observations))

    def test_emits_retryable_failure_with_only_stable_code(self) -> None:
        self.queue.enqueue(
            self.owner_id,
            "retry",
            {},
            max_attempts=2,
            now="2026-08-20T10:00:00+00:00",
        )
        observations = []

        def handler(_payload: object) -> object:
            raise RetryableTaskError("provider_timeout", "provider-key=secret")

        worker = TaskWorker(
            self.queue,
            {"retry": handler},
            worker_id="worker-a",
            observation_sink=observations.append,
        )

        self.assertTrue(worker.run_once(now="2026-08-20T10:00:01+00:00"))
        self.assertEqual(WorkerObservationOutcome.RETRYABLE_FAILURE, observations[0].outcome)
        self.assertEqual("provider_timeout", observations[0].failure_code)
        self.assertNotIn("provider-key", repr(observations))

    def test_observation_sink_failure_never_changes_queue_result(self) -> None:
        task = self.queue.enqueue(self.owner_id, "echo", {}, now="2026-08-20T10:00:00+00:00")

        def broken_sink(_observation: object) -> None:
            raise RuntimeError("telemetry backend secret")

        worker = TaskWorker(
            self.queue,
            {"echo": lambda _payload: {"ok": True}},
            worker_id="worker-a",
            observation_sink=broken_sink,
        )

        self.assertTrue(worker.run_once(now="2026-08-20T10:00:01+00:00"))
        self.assertEqual(TaskState.SUCCEEDED, self.queue.get(self.owner_id, task.id).state)


if __name__ == "__main__":
    unittest.main()
