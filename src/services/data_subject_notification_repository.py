"""Encrypted owner-scoped persistence for data-subject notifications."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
import json

from src.domain.data_subject_notifications import (
    DataSubjectNotification,
    NotificationValidationError,
)
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import (
    MetadataIntegrityError,
    MetadataStore,
    MetadataStoreError,
    require_metadata_store,
)


class DataSubjectNotificationRepositoryError(RuntimeError):
    """Stable notification failure without payload or driver details."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class DataSubjectNotificationRepository:
    _RECORD_VERSION = 1
    _AAD_PREFIX = "past-partner/data-subject-notification/v1/"
    _MAX_LIMIT = 100

    def __init__(self, metadata_store: MetadataStore, encryption: AuthenticatedEncryptionService) -> None:
        self.metadata_store = require_metadata_store(metadata_store)
        self.encryption = encryption
        self.metadata_store.migrate()

    def create(
        self,
        notification: DataSubjectNotification,
        *,
        connection: object | None = None,
    ) -> DataSubjectNotification:
        if not isinstance(notification, DataSubjectNotification):
            raise TypeError("notification must be a DataSubjectNotification")
        envelope = self._encode(notification)
        try:
            if connection is not None:
                return self._create_in_transaction(connection, notification, envelope)
            with self.metadata_store.transaction(
                immediate=self.metadata_store.backend_name == "sqlite"
            ) as transaction:
                return self._create_in_transaction(transaction, notification, envelope)
        except DataSubjectNotificationRepositoryError:
            raise
        except MetadataIntegrityError as exc:
            existing = self.get_by_operation(
                notification.owner_id, notification.event_type, notification.operation_id
            )
            if existing is not None and self._same_operation(existing, notification):
                return existing
            raise DataSubjectNotificationRepositoryError(
                "notification_exists", "notification operation already exists"
            ) from exc
        except MetadataStoreError as exc:
            raise DataSubjectNotificationRepositoryError(
                "notification_unavailable", "notification could not be persisted"
            ) from exc

    def get(self, owner_id: str, notification_id: str) -> DataSubjectNotification | None:
        owner = self._owner(owner_id)
        if not isinstance(notification_id, str) or not notification_id:
            return None
        try:
            with closing(self.metadata_store.connect()) as connection:
                row = connection.execute(
                    "SELECT id, owner_id, event_type, operation_id, status, attempts, "
                    "next_attempt_at, last_error_code, occurred_at, record_version, encrypted_payload "
                    "FROM data_subject_notifications WHERE id = ? AND owner_id = ?",
                    (notification_id, owner),
                ).fetchone()
        except MetadataStoreError as exc:
            raise DataSubjectNotificationRepositoryError(
                "notification_unavailable", "notifications are unavailable"
            ) from exc
        return self._decode(row) if row is not None else None

    def get_by_operation(
        self, owner_id: str, event_type: str, operation_id: str
    ) -> DataSubjectNotification | None:
        owner = self._owner(owner_id)
        with closing(self.metadata_store.connect()) as connection:
            row = connection.execute(
                "SELECT id, owner_id, event_type, operation_id, status, attempts, "
                "next_attempt_at, last_error_code, occurred_at, record_version, encrypted_payload "
                "FROM data_subject_notifications WHERE owner_id = ? AND event_type = ? AND operation_id = ?",
                (owner, event_type, operation_id),
            ).fetchone()
        return self._decode(row) if row is not None else None

    def list(
        self,
        owner_id: str,
        *,
        limit: int = 100,
        before: tuple[str, str] | None = None,
    ) -> list[DataSubjectNotification]:
        owner = self._owner(owner_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self._MAX_LIMIT:
            raise DataSubjectNotificationRepositoryError(
                "invalid_notification_limit", "notification limit is invalid"
            )
        parameters: list[object] = [owner]
        query = (
            "SELECT id, owner_id, event_type, operation_id, status, attempts, next_attempt_at, "
            "last_error_code, occurred_at, record_version, encrypted_payload "
            "FROM data_subject_notifications WHERE owner_id = ?"
        )
        if before is not None:
            timestamp, notification_id = self._cursor(before)
            query += " AND (occurred_at < ? OR (occurred_at = ? AND id < ?))"
            parameters.extend((timestamp, timestamp, notification_id))
        query += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        parameters.append(limit)
        try:
            with closing(self.metadata_store.connect()) as connection:
                rows = connection.execute(query, parameters).fetchall()
        except MetadataStoreError as exc:
            raise DataSubjectNotificationRepositoryError(
                "notification_unavailable", "notifications are unavailable"
            ) from exc
        return [self._decode(row) for row in rows]

    def update(self, notification: DataSubjectNotification) -> DataSubjectNotification:
        if not isinstance(notification, DataSubjectNotification):
            raise TypeError("notification must be a DataSubjectNotification")
        envelope = self._encode(notification)
        try:
            with self.metadata_store.transaction(
                immediate=self.metadata_store.backend_name == "sqlite"
            ) as connection:
                result = connection.execute(
                    "UPDATE data_subject_notifications SET status = ?, attempts = ?, "
                    "next_attempt_at = ?, last_error_code = ?, encrypted_payload = ? "
                    "WHERE id = ? AND owner_id = ?",
                    (
                        notification.status,
                        notification.attempts,
                        notification.next_attempt_at,
                        notification.last_error_code,
                        envelope,
                        notification.id,
                        notification.owner_id,
                    ),
                )
                if getattr(result, "rowcount", 0) != 1:
                    raise DataSubjectNotificationRepositoryError(
                        "notification_not_found", "notification was not found"
                    )
        except DataSubjectNotificationRepositoryError:
            raise
        except MetadataStoreError as exc:
            raise DataSubjectNotificationRepositoryError(
                "notification_unavailable", "notification could not be updated"
            ) from exc
        return notification

    def _create_in_transaction(
        self, connection: object, notification: DataSubjectNotification, envelope: bytes
    ) -> DataSubjectNotification:
        self._lock_owner(connection, notification.owner_id)
        existing_row = connection.execute(
            "SELECT id, owner_id, event_type, operation_id, status, attempts, next_attempt_at, "
            "last_error_code, occurred_at, record_version, encrypted_payload "
            "FROM data_subject_notifications WHERE owner_id = ? AND event_type = ? AND operation_id = ?",
            (notification.owner_id, notification.event_type, notification.operation_id),
        ).fetchone()
        if existing_row is not None:
            existing = self._decode(existing_row)
            if self._same_operation(existing, notification):
                return existing
            raise DataSubjectNotificationRepositoryError(
                "notification_exists", "notification operation already exists"
            )
        connection.execute(
            "INSERT INTO data_subject_notifications "
            "(id, owner_id, event_type, operation_id, status, attempts, next_attempt_at, "
            "last_error_code, occurred_at, record_version, encrypted_payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                notification.id,
                notification.owner_id,
                notification.event_type,
                notification.operation_id,
                notification.status,
                notification.attempts,
                notification.next_attempt_at,
                notification.last_error_code,
                notification.occurred_at,
                self._RECORD_VERSION,
                envelope,
            ),
        )
        return notification

    @staticmethod
    def _lock_owner(connection: object, owner_id: str) -> None:
        result = connection.execute("UPDATE local_users SET id = id WHERE id = ?", (owner_id,))
        if getattr(result, "rowcount", 0) != 1:
            raise DataSubjectNotificationRepositoryError(
                "notification_owner_invalid", "notification owner is invalid"
            )

    def _encode(self, notification: DataSubjectNotification) -> bytes:
        payload = json.dumps(
            notification.to_storage_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return self.encryption.encrypt(payload, self._aad(notification.owner_id, notification.id))

    def _decode(self, row: Iterable[object]) -> DataSubjectNotification:
        values = tuple(row)
        if len(values) != 11:
            raise DataSubjectNotificationRepositoryError(
                "notification_record_corrupt", "notification record is invalid"
            )
        (
            notification_id,
            owner_id,
            event_type,
            operation_id,
            status,
            attempts,
            next_attempt_at,
            last_error_code,
            occurred_at,
            version,
            envelope,
        ) = values
        if version != self._RECORD_VERSION or not isinstance(envelope, bytes):
            raise DataSubjectNotificationRepositoryError(
                "notification_record_corrupt", "notification record is invalid"
            )
        try:
            plaintext = self.encryption.decrypt(envelope, self._aad(str(owner_id), str(notification_id)))
            notification = DataSubjectNotification.from_storage_dict(json.loads(plaintext.decode("utf-8")))
        except (
            AuthenticationError,
            InvalidEncryptedPayloadError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            NotificationValidationError,
        ) as exc:
            raise DataSubjectNotificationRepositoryError(
                "notification_record_corrupt", "notification record is invalid"
            ) from exc
        if (
            notification.id != notification_id
            or notification.owner_id != owner_id
            or notification.event_type != event_type
            or notification.operation_id != operation_id
            or notification.status != status
            or notification.attempts != attempts
            or notification.next_attempt_at != next_attempt_at
            or notification.last_error_code != last_error_code
            or notification.occurred_at != occurred_at
        ):
            raise DataSubjectNotificationRepositoryError(
                "notification_record_corrupt", "notification record is invalid"
            )
        return notification

    @classmethod
    def _aad(cls, owner_id: str, notification_id: str) -> bytes:
        return f"{cls._AAD_PREFIX}{owner_id}/{notification_id}".encode("utf-8")

    @staticmethod
    def _same_operation(
        first: DataSubjectNotification, second: DataSubjectNotification
    ) -> bool:
        return (
            first.owner_id == second.owner_id
            and first.event_type == second.event_type
            and first.operation_id == second.operation_id
            and dict(first.counts) == dict(second.counts)
            and first.occurred_at == second.occurred_at
        )

    @staticmethod
    def _owner(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DataSubjectNotificationRepositoryError(
                "notification_owner_invalid", "notification owner is invalid"
            )
        return value.strip()

    @staticmethod
    def _cursor(value: object) -> tuple[str, str]:
        if not isinstance(value, tuple) or len(value) != 2:
            raise DataSubjectNotificationRepositoryError(
                "invalid_notification_cursor", "notification cursor is invalid"
            )
        timestamp, notification_id = value
        if not isinstance(timestamp, str) or not isinstance(notification_id, str) or not notification_id:
            raise DataSubjectNotificationRepositoryError(
                "invalid_notification_cursor", "notification cursor is invalid"
            )
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise DataSubjectNotificationRepositoryError(
                "invalid_notification_cursor", "notification cursor is invalid"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise DataSubjectNotificationRepositoryError(
                "invalid_notification_cursor", "notification cursor is invalid"
            )
        return parsed.astimezone(UTC).isoformat(), notification_id


__all__ = [
    "DataSubjectNotificationRepository",
    "DataSubjectNotificationRepositoryError",
]
