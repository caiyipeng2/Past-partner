"""Vendor-neutral task notification publishing and a deterministic test broker."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from src.domain.task_broker import BrokerDelivery, TaskNotification
from src.services.task_queue import TaskQueue, TaskQueueError


class TaskBrokerError(RuntimeError):
    """Stable broker error that never contains driver details or secrets."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@runtime_checkable
class TaskBroker(Protocol):
    """Minimal broker port; production adapters can implement it later."""

    def publish(self, notification: TaskNotification) -> str: ...

    def receive(self, consumer_id: str, *, timeout_seconds: float = 0) -> BrokerDelivery | None: ...

    def ack(self, delivery_id: str, consumer_id: str) -> None: ...

    def nack(self, delivery_id: str, consumer_id: str, *, requeue: bool = True) -> None: ...


@dataclass(frozen=True, slots=True)
class BrokerPublishStats:
    """Redacted result of one bounded outbox drain."""

    scanned: int
    published: int
    failed: int

    def to_mapping(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "published": self.published,
            "failed": self.failed,
        }


class TaskBrokerPublisher:
    """Drain the durable outbox with publish-before-mark idempotency."""

    _MAX_BATCH = 1000

    def __init__(self, queue: TaskQueue, broker: TaskBroker):
        if not isinstance(queue, TaskQueue):
            raise TypeError("queue must be a TaskQueue")
        if not isinstance(broker, TaskBroker) or not all(
            callable(getattr(broker, method, None))
            for method in ("publish", "receive", "ack", "nack")
        ):
            raise TypeError("broker must implement the TaskBroker contract")
        self.queue = queue
        self.broker = broker

    def publish_once(self, *, limit: int = 100, now: str | None = None) -> BrokerPublishStats:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self._MAX_BATCH:
            raise TaskBrokerError("broker_batch_invalid", "broker batch limit is invalid")
        pending = self.queue.list_broker_notifications(now=now, limit=limit)
        published = 0
        failed = 0
        for notification in pending:
            try:
                self.broker.publish(notification)
            except TaskBrokerError as exc:
                failed += 1
                self._defer(notification.task_id, exc.code, now)
                continue
            except Exception:
                failed += 1
                self._defer(notification.task_id, "broker_publish_failed", now)
                continue
            try:
                marked = self.queue.mark_broker_notification_published(notification.task_id, now=now)
            except TaskQueueError as exc:
                raise TaskBrokerError("broker_outbox_update_failed", "broker outbox update failed") from exc
            if marked:
                published += 1
        return BrokerPublishStats(len(pending), published, failed)

    def _defer(self, task_id: str, error_code: str, now: str | None) -> None:
        try:
            self.queue.defer_broker_notification(task_id, error_code, now=now)
        except TaskQueueError as exc:
            raise TaskBrokerError("broker_outbox_update_failed", "broker outbox update failed") from exc


class InMemoryTaskBroker:
    """Thread-safe broker used only for deterministic contract tests.

    It implements duplicate suppression and visibility recovery so tests exercise
    the delivery contract without adding Redis/RabbitMQ credentials or network
    state to the repository.
    """

    def __init__(self, *, visibility_timeout_seconds: int = 30):
        if isinstance(visibility_timeout_seconds, bool) or not isinstance(visibility_timeout_seconds, int):
            raise TaskBrokerError("visibility_timeout_invalid", "visibility timeout is invalid")
        if not 1 <= visibility_timeout_seconds <= 3600:
            raise TaskBrokerError("visibility_timeout_invalid", "visibility timeout is invalid")
        self._visibility_timeout = visibility_timeout_seconds
        self._lock = RLock()
        self._ready: deque[TaskNotification] = deque()
        self._known: dict[str, TaskNotification] = {}
        self._acked: set[str] = set()
        self._attempts: dict[str, int] = {}
        self._in_flight: dict[str, tuple[str, TaskNotification, datetime]] = {}
        self._next_publish_error: str | None = None

    def publish(self, notification: TaskNotification) -> str:
        if not isinstance(notification, TaskNotification):
            raise TaskBrokerError("notification_invalid", "notification is invalid")
        with self._lock:
            if self._next_publish_error is not None:
                _code = self._next_publish_error
                self._next_publish_error = None
                raise TaskBrokerError("broker_unavailable", "broker publish failed")
            if notification.message_id in self._known or notification.message_id in self._acked:
                return notification.message_id
            self._known[notification.message_id] = notification
            self._ready.append(notification)
            return notification.message_id

    def fail_next_publish(self, _detail: str = "") -> None:
        """Inject one stable failure; the detail is intentionally discarded."""

        with self._lock:
            self._next_publish_error = "broker_unavailable"

    def receive(self, consumer_id: str, *, timeout_seconds: float = 0) -> BrokerDelivery | None:
        self._validate_consumer(consumer_id)
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TaskBrokerError("receive_timeout_invalid", "receive timeout is invalid")
        if timeout_seconds < 0 or timeout_seconds > 300:
            raise TaskBrokerError("receive_timeout_invalid", "receive timeout is invalid")
        with self._lock:
            self._recover_expired_locked(datetime.now(UTC))
            if not self._ready:
                return None
            notification = self._ready.popleft()
            attempt = self._attempts.get(notification.message_id, 0) + 1
            self._attempts[notification.message_id] = attempt
            delivery_id = uuid4().hex
            self._in_flight[delivery_id] = (
                consumer_id,
                notification,
                datetime.now(UTC) + timedelta(seconds=self._visibility_timeout),
            )
            return BrokerDelivery(delivery_id, consumer_id, notification, attempt)

    def ack(self, delivery_id: str, consumer_id: str) -> None:
        consumer, notification = self._take_delivery(delivery_id, consumer_id)
        del consumer
        with self._lock:
            self._acked.add(notification.message_id)

    def nack(self, delivery_id: str, consumer_id: str, *, requeue: bool = True) -> None:
        _consumer, notification = self._take_delivery(delivery_id, consumer_id)
        with self._lock:
            if requeue:
                self._ready.appendleft(notification)
            else:
                self._acked.add(notification.message_id)

    def recover_expired(self, *, now: datetime | None = None) -> int:
        current = datetime.now(UTC) if now is None else now
        if current.tzinfo is None:
            raise TaskBrokerError("receive_time_invalid", "recovery time is invalid")
        with self._lock:
            return self._recover_expired_locked(current)

    def _recover_expired_locked(self, now: datetime) -> int:
        expired = [
            delivery_id
            for delivery_id, (_consumer, _notification, deadline) in self._in_flight.items()
            if deadline <= now
        ]
        for delivery_id in expired:
            _consumer, notification, _deadline = self._in_flight.pop(delivery_id)
            self._ready.appendleft(notification)
        return len(expired)

    def _take_delivery(self, delivery_id: str, consumer_id: str) -> tuple[str, TaskNotification]:
        self._validate_consumer(consumer_id)
        if not isinstance(delivery_id, str) or not delivery_id:
            raise TaskBrokerError("delivery_not_found", "delivery was not found")
        with self._lock:
            delivery = self._in_flight.get(delivery_id)
            if delivery is None:
                raise TaskBrokerError("delivery_not_found", "delivery was not found")
            owner, notification, _deadline = delivery
            if owner != consumer_id:
                raise TaskBrokerError("delivery_consumer_mismatch", "delivery consumer mismatch")
            self._in_flight.pop(delivery_id)
            return owner, notification

    @staticmethod
    def _validate_consumer(consumer_id: str) -> None:
        if not isinstance(consumer_id, str) or not consumer_id or len(consumer_id) > 128:
            raise TaskBrokerError("consumer_invalid", "consumer ID is invalid")


__all__ = [
    "BrokerPublishStats",
    "InMemoryTaskBroker",
    "TaskBroker",
    "TaskBrokerError",
    "TaskBrokerPublisher",
]
