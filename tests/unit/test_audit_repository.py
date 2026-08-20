from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import shutil
import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.audit_events import AuditAction, AuditEvent, AuditOutcome
from src.services.audit_repository import AuditRepository, AuditRepositoryError
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.local_auth import LocalAuthService
from src.services.master_key import EnvironmentMasterKeyProvider, MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.storage import StorageLayout


class AuditRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"a" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(self.layout.database_path(), self.encryption, mode="test")
        self.repository = AuditRepository(self.auth.metadata_store, self.encryption)
        self.owner_id = self.auth.owner_id

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def event(self, *, event_id: str, owner_id: str | None = None, seconds: int = 0) -> AuditEvent:
        return AuditEvent(
            id=event_id,
            owner_id=owner_id or self.owner_id,
            action=AuditAction.PERSONA_DELETED,
            outcome=AuditOutcome.SUCCESS,
            resource_type="persona",
            resource_id=f"persona-{event_id}",
            occurred_at=(datetime(2026, 8, 20, 10, 0, tzinfo=UTC) + timedelta(seconds=seconds)).isoformat(),
            metadata={"deleted_children": seconds, "reason_code": "user_requested"},
        )

    def test_append_and_list_are_owner_scoped_and_encrypted(self) -> None:
        first = self.event(event_id="evt-1", seconds=1)
        second = self.event(event_id="evt-2", seconds=2)
        self.repository.append(first)
        self.repository.append(second)

        self.assertEqual([second, first], self.repository.list(self.owner_id))
        self.assertEqual([], self.repository.list("other-owner"))
        self.assertNotIn(b"user_requested", self.layout.database_path().read_bytes())

    def test_list_supports_bounded_cursor_pagination(self) -> None:
        events = [self.event(event_id=f"evt-{index}", seconds=index) for index in range(3)]
        for event in events:
            self.repository.append(event)

        page = self.repository.list(self.owner_id, limit=2)
        self.assertEqual([events[2], events[1]], page)
        next_page = self.repository.list(
            self.owner_id,
            limit=2,
            before=(page[-1].occurred_at, page[-1].id),
        )
        self.assertEqual([events[0]], next_page)

        with self.assertRaisesRegex(AuditRepositoryError, "limit"):
            self.repository.list(self.owner_id, limit=101)

    def test_corrupt_envelope_fails_closed_without_driver_details(self) -> None:
        event = self.event(event_id="evt-corrupt")
        self.repository.append(event)
        with sqlite3.connect(self.layout.database_path()) as connection:
            connection.execute(
                "UPDATE audit_events SET encrypted_payload = ? WHERE id = ?",
                (b"corrupt", event.id),
            )

        with self.assertRaises(AuditRepositoryError) as captured:
            self.repository.list(self.owner_id)
        self.assertEqual("audit_record_corrupt", captured.exception.code)
        self.assertNotIn("corrupt", str(captured.exception))

    def test_append_rejects_duplicate_ids(self) -> None:
        event = self.event(event_id="evt-duplicate")
        self.repository.append(event)
        with self.assertRaises(AuditRepositoryError) as captured:
            self.repository.append(event)
        self.assertEqual("audit_event_exists", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
