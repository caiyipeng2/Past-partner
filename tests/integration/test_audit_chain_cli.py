from __future__ import annotations

import base64
from contextlib import redirect_stdout
from datetime import UTC, datetime
import io
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from uuid import uuid4

from scripts.verify_audit_chain import main
from src.domain.audit_events import AuditAction, AuditEvent, AuditOutcome
from src.services.audit_repository import AuditRepository
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.local_auth import LocalAuthService
from src.services.master_key import EnvironmentMasterKeyProvider, MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.storage import StorageLayout


class AuditChainCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"c" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(layout.database_path(), self.encryption, mode="test")
        self.repository = AuditRepository(self.auth.metadata_store, self.encryption)
        self.database_path = layout.database_path()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def event(self, event_id: str) -> AuditEvent:
        return AuditEvent(
            id=event_id,
            owner_id=self.auth.owner_id,
            action=AuditAction.IMPORT_DELETED,
            outcome=AuditOutcome.SUCCESS,
            resource_type="import",
            resource_id=f"import-{event_id}",
            occurred_at=datetime.now(UTC).isoformat(),
        )

    def run_cli(self, *arguments: str) -> tuple[int, dict[str, object], str]:
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = main([*arguments])
        raw = output.getvalue()
        return exit_code, json.loads(raw), raw

    def test_verify_command_emits_redacted_success(self) -> None:
        self.repository.append(self.event("evt-1"))

        exit_code, payload, raw = self.run_cli(
            "--database", str(self.database_path), "--owner", self.auth.owner_id
        )

        self.assertEqual(0, exit_code)
        self.assertTrue(payload["ok"])
        self.assertEqual(1, payload["event_count"])
        self.assertNotIn(str(self.database_path), raw)

    def test_verify_command_returns_stable_failure_without_input_path(self) -> None:
        event = self.event("evt-tamper")
        self.repository.append(event)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE audit_events SET previous_hash = ? WHERE id = ?",
                ("d" * 64, event.id),
            )

        exit_code, payload, raw = self.run_cli(
            "--database", str(self.database_path), "--owner", self.auth.owner_id
        )

        self.assertEqual(1, exit_code)
        self.assertFalse(payload["ok"])
        self.assertEqual("audit_chain_mismatch", payload["error"]["code"])
        self.assertNotIn(str(self.database_path), raw)


if __name__ == "__main__":
    unittest.main()
