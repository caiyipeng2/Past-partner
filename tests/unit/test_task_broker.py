"""R1-04 durable task notification and broker contracts."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.task_broker import BrokerDelivery, TaskNotification
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.local_auth import LocalAuthService
from src.services.master_key import (
    EnvironmentMasterKeyProvider,
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
)
from src.services.storage import StorageLayout
from src.services.task_broker import (
    InMemoryTaskBroker,
    TaskBrokerError,
    TaskBrokerPublisher,
)
from src.services.task_queue import TaskQueue


class TaskBrokerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"b" * MASTER_KEY_BYTES).decode("ascii")
        encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        auth = LocalAuthService(layout.database_path(), encryption, mode="test")
        self.queue = TaskQueue(auth.metadata_store, encryption)
        self.owner_id = auth.owner_id

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_enqueue_stages_one_redacted_notification_in_same_database(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "integration.echo",
            {"secret": "never-in-broker"},
            now="2026-08-25T10:00:00+00:00",
        )

        pending = self.queue.list_broker_notifications(now="2026-08-25T10:00:01+00:00")

        self.assertEqual(
            [TaskNotification(task.id, task.id, task.task_type, task.created_at)],
            pending,
        )
        self.assertNotIn(b"never-in-broker", self.queue.metadata_store.database_path.read_bytes())
        self.assertNotIn("owner_id", pending[0].to_mapping())
        self.assertNotIn("payload", pending[0].to_mapping())

    def test_publisher_is_idempotent_and_marks_outbox_after_publish(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "integration.echo",
            {"value": 1},
            now="2026-08-25T10:00:00+00:00",
        )
        broker = InMemoryTaskBroker()
        publisher = TaskBrokerPublisher(self.queue, broker)

        first = publisher.publish_once(now="2026-08-25T10:00:01+00:00")
        second = publisher.publish_once(now="2026-08-25T10:00:02+00:00")
        delivery = broker.receive("worker-a")

        self.assertEqual((1, 0), (first.published, first.failed))
        self.assertEqual((0, 0), (second.published, second.failed))
        self.assertIsNotNone(delivery)
        self.assertEqual(task.id, delivery.notification.task_id)
        self.assertEqual(task.task_type, delivery.notification.task_type)

    def test_failed_publish_is_deferred_without_leaking_adapter_error(self) -> None:
        task = self.queue.enqueue(
            self.owner_id,
            "integration.echo",
            {"value": "secret"},
            now="2026-08-25T10:00:00+00:00",
        )
        broker = InMemoryTaskBroker()
        broker.fail_next_publish("driver details must stay private")
        publisher = TaskBrokerPublisher(self.queue, broker)

        result = publisher.publish_once(now="2026-08-25T10:00:01+00:00")
        pending_now = self.queue.list_broker_notifications(now="2026-08-25T10:00:02+00:00")
        pending_later = self.queue.list_broker_notifications(now="2026-08-25T10:00:07+00:00")

        self.assertEqual((0, 1), (result.published, result.failed))
        self.assertEqual([], pending_now)
        self.assertEqual([task.id], [item.task_id for item in pending_later])
        self.assertNotIn("driver details", result.to_mapping().__repr__())

    def test_ack_requires_the_receiving_consumer_and_nack_redelivers(self) -> None:
        notification = TaskNotification("task-1", "task-1", "integration.echo", "2026-08-25T10:00:00+00:00")
        broker = InMemoryTaskBroker(visibility_timeout_seconds=1)
        broker.publish(notification)
        delivery = broker.receive("worker-a")
        self.assertIsInstance(delivery, BrokerDelivery)

        with self.assertRaises(TaskBrokerError) as mismatch:
            broker.ack(delivery.delivery_id, "worker-b")
        self.assertEqual("delivery_consumer_mismatch", mismatch.exception.code)

        broker.nack(delivery.delivery_id, "worker-a", requeue=True)
        redelivered = broker.receive("worker-b")
        self.assertIsNotNone(redelivered)
        self.assertEqual(notification.message_id, redelivered.notification.message_id)
        broker.ack(redelivered.delivery_id, "worker-b")
        self.assertIsNone(broker.receive("worker-a"))

    def test_visibility_timeout_recovers_unacked_delivery(self) -> None:
        notification = TaskNotification("task-2", "task-2", "integration.echo", "2026-08-25T10:00:00+00:00")
        broker = InMemoryTaskBroker(visibility_timeout_seconds=1)
        broker.publish(notification)
        first = broker.receive("worker-a")
        self.assertIsNotNone(first)

        recovered = broker.recover_expired(
            now=datetime.now(UTC) + timedelta(seconds=2)
        )
        second = broker.receive("worker-b")

        self.assertEqual(1, recovered)
        self.assertIsNotNone(second)
        self.assertEqual(notification.message_id, second.notification.message_id)


if __name__ == "__main__":
    unittest.main()
