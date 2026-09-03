"""R2-04 Task 4 data-subject notification contracts."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import shutil
import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.data_subject_notifications import (
    DataSubjectNotification,
    NotificationValidationError,
)
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.data_subject_notification_repository import (
    DataSubjectNotificationRepository,
)
from src.services.data_subject_notification_service import (
    DataSubjectNotificationService,
)
from src.services.local_auth import LocalAuthService
from src.services.master_key import (
    EnvironmentMasterKeyProvider,
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
)
from src.services.storage import StorageLayout


class DataSubjectNotificationDomainTests(unittest.TestCase):
    def notification(self, **changes) -> DataSubjectNotification:
        values = {
            "id": "notification-1",
            "owner_id": "owner-1",
            "event_type": "export_completed",
            "operation_id": "export-1",
            "counts": {"personas": 1, "imports": 2},
            "occurred_at": "2026-09-03T00:00:00+00:00",
        }
        values.update(changes)
        return DataSubjectNotification(**values)

    def test_public_view_contains_only_bounded_lifecycle_metadata(self) -> None:
        notification = self.notification()

        public = notification.to_public_dict()

        self.assertNotIn("owner_id", public)
        self.assertEqual("export_completed", public["event_type"])
        self.assertEqual({"personas": 1, "imports": 2}, public["counts"])
        self.assertEqual("pending", public["status"])
        self.assertEqual(0, public["attempts"])

    def test_rejects_unapproved_event_payload_and_invalid_delivery_state(self) -> None:
        for changes in (
            {"event_type": "chat_message"},
            {"counts": {"raw_message": 1}},
            {"status": "pending", "last_error_code": "network_error"},
            {"occurred_at": "2026-09-03T00:00:00"},
        ):
            with self.subTest(changes=changes), self.assertRaises(NotificationValidationError):
                self.notification(**changes)

    def test_failed_notification_can_be_retried_and_delivered(self) -> None:
        notification = self.notification().mark_failed(
            "provider_timeout", "2026-09-03T00:01:00+00:00"
        )
        retrying = notification.retry("2026-09-03T00:02:00+00:00")
        delivered = retrying.mark_delivered("2026-09-03T00:03:00+00:00")

        self.assertEqual("failed", notification.status)
        self.assertEqual(1, notification.attempts)
        self.assertEqual("pending", retrying.status)
        self.assertEqual(2, delivered.attempts)
        self.assertEqual("delivered", delivered.status)
        self.assertIsNone(delivered.last_error_code)


class DataSubjectNotificationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"n" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(layout.database_path(), self.encryption, mode="test")
        self.repository = DataSubjectNotificationRepository(
            self.auth.metadata_store, self.encryption
        )
        self.service = DataSubjectNotificationService(self.repository)
        self.owner_id = self.auth.owner_id

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_records_are_encrypted_owner_scoped_and_state_updates_are_bounded(self) -> None:
        notification = self.service.record_export(
            self.owner_id,
            operation_id="export-1",
            counts={"personas": 1},
            occurred_at="2026-09-03T00:00:00+00:00",
        )
        failed = self.service.mark_failed(
            self.owner_id,
            notification.id,
            error_code="provider_timeout",
            next_attempt_at="2026-09-03T00:01:00+00:00",
        )

        listed = self.service.list(self.owner_id)
        self.assertEqual([failed], listed)
        self.assertEqual([], self.service.list("other-owner"))
        self.assertEqual("failed", listed[0].status)
        self.assertEqual(1, listed[0].attempts)

        with sqlite3.connect(StorageLayout(self.root).database_path()) as connection:
            envelope = connection.execute(
                "SELECT encrypted_payload FROM data_subject_notifications WHERE id = ?",
                (notification.id,),
            ).fetchone()[0]
        self.assertNotIn(b"export-1", envelope)
        self.assertNotIn(b"personas", envelope)

        delivered = self.service.mark_delivered(self.owner_id, notification.id)
        self.assertEqual("delivered", delivered.status)
        self.assertEqual(2, delivered.attempts)

    def test_operation_identity_is_idempotent_for_replayed_export_event(self) -> None:
        first = self.service.record_export(
            self.owner_id,
            operation_id="export-1",
            counts={"personas": 1},
            occurred_at="2026-09-03T00:00:00+00:00",
        )
        replay = self.service.record_export(
            self.owner_id,
            operation_id="export-1",
            counts={"personas": 1},
            occurred_at="2026-09-03T00:00:00+00:00",
        )

        self.assertEqual(first, replay)
        self.assertEqual(1, len(self.service.list(self.owner_id)))


if __name__ == "__main__":
    unittest.main()
