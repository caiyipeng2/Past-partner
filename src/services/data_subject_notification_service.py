"""Owner lifecycle service for data-subject notification records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping
from uuid import uuid4

from src.domain.data_subject_notifications import (
    DataSubjectNotification,
    NotificationValidationError,
)
from src.services.data_subject_notification_repository import (
    DataSubjectNotificationRepository,
    DataSubjectNotificationRepositoryError,
)


class DataSubjectNotificationServiceError(RuntimeError):
    """Stable service error without payloads or provider details."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class DataSubjectNotificationService:
    def __init__(self, repository: DataSubjectNotificationRepository):
        self.repository = repository

    def record_export(
        self,
        owner_id: str,
        *,
        operation_id: str | None = None,
        counts: Mapping[str, int] | None = None,
        occurred_at: str | None = None,
        connection: object | None = None,
    ) -> DataSubjectNotification:
        return self._record(
            owner_id,
            event_type="export_completed",
            operation_id=operation_id or uuid4().hex,
            counts=counts,
            occurred_at=occurred_at,
            connection=connection,
        )

    def record_deletion(
        self,
        owner_id: str,
        *,
        operation_id: str,
        counts: Mapping[str, int],
        occurred_at: str,
        connection: object | None = None,
    ) -> DataSubjectNotification:
        return self._record(
            owner_id,
            event_type="deletion_completed",
            operation_id=operation_id,
            counts=counts,
            occurred_at=occurred_at,
            connection=connection,
        )

    def list(
        self,
        owner_id: str,
        *,
        limit: int = 100,
        before: tuple[str, str] | None = None,
    ) -> list[DataSubjectNotification]:
        try:
            return self.repository.list(owner_id, limit=limit, before=before)
        except DataSubjectNotificationRepositoryError as exc:
            raise DataSubjectNotificationServiceError(exc.code, str(exc)) from exc

    def mark_failed(
        self,
        owner_id: str,
        notification_id: str,
        *,
        error_code: str,
        next_attempt_at: str,
    ) -> DataSubjectNotification:
        notification = self._get(owner_id, notification_id)
        try:
            return self.repository.update(notification.mark_failed(error_code, next_attempt_at))
        except NotificationValidationError as exc:
            raise DataSubjectNotificationServiceError(exc.code, str(exc)) from exc
        except DataSubjectNotificationRepositoryError as exc:
            raise DataSubjectNotificationServiceError(exc.code, str(exc)) from exc

    def retry(self, owner_id: str, notification_id: str, *, now: str | None = None) -> DataSubjectNotification:
        notification = self._get(owner_id, notification_id)
        try:
            return self.repository.update(notification.retry(now or _utc_now()))
        except NotificationValidationError as exc:
            raise DataSubjectNotificationServiceError(exc.code, str(exc)) from exc
        except DataSubjectNotificationRepositoryError as exc:
            raise DataSubjectNotificationServiceError(exc.code, str(exc)) from exc

    def mark_delivered(
        self, owner_id: str, notification_id: str, *, now: str | None = None
    ) -> DataSubjectNotification:
        notification = self._get(owner_id, notification_id)
        try:
            return self.repository.update(notification.mark_delivered(now or _utc_now()))
        except NotificationValidationError as exc:
            raise DataSubjectNotificationServiceError(exc.code, str(exc)) from exc
        except DataSubjectNotificationRepositoryError as exc:
            raise DataSubjectNotificationServiceError(exc.code, str(exc)) from exc

    def _record(
        self,
        owner_id: str,
        *,
        event_type: str,
        operation_id: str,
        counts: Mapping[str, int] | None,
        occurred_at: str | None,
        connection: object | None,
    ) -> DataSubjectNotification:
        try:
            notification = DataSubjectNotification.create(
                owner_id=owner_id,
                event_type=event_type,
                operation_id=operation_id,
                counts=counts,
                occurred_at=occurred_at or _utc_now(),
            )
            return self.repository.create(notification, connection=connection)
        except NotificationValidationError as exc:
            raise DataSubjectNotificationServiceError(exc.code, str(exc)) from exc
        except DataSubjectNotificationRepositoryError as exc:
            raise DataSubjectNotificationServiceError(exc.code, str(exc)) from exc

    def _get(self, owner_id: str, notification_id: str) -> DataSubjectNotification:
        try:
            notification = self.repository.get(owner_id, notification_id)
        except DataSubjectNotificationRepositoryError as exc:
            raise DataSubjectNotificationServiceError(exc.code, str(exc)) from exc
        if notification is None:
            raise DataSubjectNotificationServiceError(
                "notification_not_found", "notification was not found"
            )
        return notification


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = ["DataSubjectNotificationService", "DataSubjectNotificationServiceError"]
