"""P4-06 durable encrypted task queue behavior."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import shutil
import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.task_queue import TaskState
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.local_auth import LocalAuthService
from src.services.master_key import (
    EnvironmentMasterKeyProvider,
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
)
from src.services.storage import StorageLayout
from src.services.task_queue import TaskQueue, TaskQueueError


class TaskQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"q" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(self.layout.database_path(), self.encryption, mode="test")
        self.queue = TaskQueue(self.auth.metadata_store, self.encryption)
        self.owner_id = self.auth.owner_id

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_queue_contract_rejects_invalid_task_metadata(self) -> None:
        with self.assertRaises(TaskQueueError) as empty_type:
            self.queue.enqueue(self.owner_id, "", {"value": 1})
        self.assertEqual("task_invalid", empty_type.exception.code)

        with self.assertRaises(TaskQueueError) as attempts:
            self.queue.enqueue(self.owner_id, "import", {}, max_attempts=0)
        self.assertEqual("task_invalid", attempts.exception.code)

        with self.assertRaises(TaskQueueError) as payload:
            self.queue.enqueue(self.owner_id, "import", {"value": "x" * 20_000})
        self.assertEqual("task_payload_too_large", payload.exception.code)

    def test_enqueue_get_and_list_are_owner_scoped_and_payload_is_encrypted(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "import.parse",
            {"import_id": "imp-1", "secret": "do-not-store-plaintext"},
            now="2026-08-20T10:00:00+00:00",
        )

        stored = self.queue.get(self.owner_id, task.id)

        self.assertEqual(task, stored)
        self.assertEqual([], self.queue.list("other-owner"))
        self.assertNotIn(b"do-not-store-plaintext", self.layout.database_path().read_bytes())

    def test_claim_lease_expiry_and_renewal_prevent_duplicate_work(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "import.parse",
            {"import_id": "imp-1"},
            now="2026-08-20T10:00:00+00:00",
        )

        first = self.queue.claim("worker-a", now="2026-08-20T10:00:01+00:00", lease_seconds=30)
        self.assertEqual(task.id, first.id)
        self.assertEqual(TaskState.LEASED, first.state)
        self.assertIsNone(self.queue.claim("worker-b", now="2026-08-20T10:00:02+00:00", lease_seconds=30))

        renewed = self.queue.renew(
            self.owner_id,
            task.id,
            "worker-a",
            now="2026-08-20T10:00:10+00:00",
            lease_seconds=30,
        )
        self.assertGreater(renewed.leased_until, first.leased_until)

        with self.assertRaises(TaskQueueError) as mismatch:
            self.queue.complete(self.owner_id, task.id, "worker-b")
        self.assertEqual("task_lease_owner_mismatch", mismatch.exception.code)

        reclaimed = self.queue.claim("worker-b", now="2026-08-20T10:00:41+00:00", lease_seconds=30)
        self.assertEqual("worker-b", reclaimed.lease_owner)
        self.assertEqual(2, reclaimed.attempts)

    def test_retry_is_bounded_and_completion_is_terminal(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "training.submit",
            {"job_id": "job-1"},
            max_attempts=2,
            now="2026-08-20T10:00:00+00:00",
        )
        first = self.queue.claim("worker-a", now="2026-08-20T10:00:01+00:00", lease_seconds=30)
        retried = self.queue.fail(
            self.owner_id,
            task.id,
            "worker-a",
            "provider_timeout",
            retryable=True,
            now="2026-08-20T10:00:02+00:00",
        )
        self.assertEqual(TaskState.QUEUED, retried.state)
        self.assertEqual(1, retried.attempts)

        second = self.queue.claim("worker-b", now="2026-08-20T10:00:03+00:00", lease_seconds=30)
        failed = self.queue.fail(
            self.owner_id,
            task.id,
            "worker-b",
            "provider_timeout",
            retryable=True,
            now="2026-08-20T10:00:04+00:00",
        )
        self.assertEqual(TaskState.FAILED, failed.state)
        self.assertFalse(failed.retryable)

        with self.assertRaises(TaskQueueError) as closed:
            self.queue.complete(self.owner_id, task.id, "worker-b")
        self.assertEqual("task_closed", closed.exception.code)

    def test_owner_can_cancel_queued_or_leased_task(self) -> None:
        task = self.queue.enqueue(self.owner_id, "cleanup", {}, now="2026-08-20T10:00:00+00:00")
        self.queue.claim("worker-a", now="2026-08-20T10:00:01+00:00", lease_seconds=30)

        cancelled = self.queue.cancel(self.owner_id, task.id, now="2026-08-20T10:00:02+00:00")

        self.assertEqual(TaskState.CANCELLED, cancelled.state)
        self.assertIsNone(cancelled.lease_owner)

    def test_success_clears_previous_retry_failure(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "import.parse",
            {},
            max_attempts=2,
            now="2026-08-20T10:00:00+00:00",
        )
        self.queue.claim("worker-a", now="2026-08-20T10:00:01+00:00")
        self.queue.fail(
            self.owner_id,
            task.id,
            "worker-a",
            "provider_timeout",
            retryable=True,
            now="2026-08-20T10:00:02+00:00",
        )
        self.queue.claim("worker-b", now="2026-08-20T10:00:03+00:00")
        completed = self.queue.complete(
            self.owner_id,
            task.id,
            "worker-b",
            result={"ok": True},
            now="2026-08-20T10:00:04+00:00",
        )
        self.assertEqual(TaskState.SUCCEEDED, completed.state)
        self.assertIsNone(completed.failure_code)

    def test_invalid_worker_and_failure_identifiers_are_stable_errors(self) -> None:
        task = self.queue.enqueue(self.owner_id, "cleanup", {}, now="2026-08-20T10:00:00+00:00")
        with self.assertRaises(TaskQueueError) as worker:
            self.queue.claim("worker with spaces", now="2026-08-20T10:00:01+00:00")
        self.assertEqual("task_invalid", worker.exception.code)

        self.queue.claim("worker-a", now="2026-08-20T10:00:01+00:00")
        with self.assertRaises(TaskQueueError) as failure:
            self.queue.fail(
                self.owner_id,
                task.id,
                "worker-a",
                "Provider Failure",
                retryable=False,
                now="2026-08-20T10:00:02+00:00",
            )
        self.assertEqual("task_invalid", failure.exception.code)


if __name__ == "__main__":
    unittest.main()
