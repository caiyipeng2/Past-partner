from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import shutil
import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.usage_records import BillingMode, UsageOperation, UsageRecord, UsageStatus
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.local_auth import LocalAuthService
from src.services.master_key import EnvironmentMasterKeyProvider, MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.storage import StorageLayout
from src.services.usage_repository import UsageRepository, UsageRepositoryError


class UsageRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"u" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(self.layout.database_path(), self.encryption, mode="test")
        self.repository = UsageRepository(self.auth.metadata_store, self.encryption)
        self.owner_id = self.auth.owner_id

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def record(self, *, record_id: str, owner_id: str | None = None, seconds: int = 0, fingerprint: str | None = None) -> UsageRecord:
        return UsageRecord(
            id=record_id,
            owner_id=owner_id or self.owner_id,
            operation=UsageOperation.CHAT,
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            billing_mode=BillingMode.PLATFORM_BILLED,
            occurred_at=(datetime(2026, 8, 20, 10, 0, tzinfo=UTC) + timedelta(seconds=seconds)).isoformat(),
            input_tokens=10 + seconds,
            output_tokens=4,
            media_units=0,
            currency="USD",
            provider_estimated_cost=0.01,
            platform_charge=0.01,
            status=UsageStatus.PRICED,
            provider_request_fingerprint=fingerprint,
        )

    def test_append_list_are_encrypted_and_owner_scoped(self) -> None:
        first = self.record(record_id="usage-1", seconds=1, fingerprint="a" * 64)
        second = self.record(record_id="usage-2", seconds=2, fingerprint="b" * 64)
        self.repository.append(first)
        self.repository.append(second)

        self.assertEqual([second, first], self.repository.list(self.owner_id))
        self.assertEqual([], self.repository.list("other-owner"))
        with sqlite3.connect(self.layout.database_path()) as connection:
            payloads = [row[0] for row in connection.execute(
                "SELECT encrypted_payload FROM usage_records"
            ).fetchall()]
        self.assertTrue(payloads)
        self.assertTrue(all(b"provider_estimated_cost" not in payload for payload in payloads))

    def test_duplicate_provider_reference_is_idempotent(self) -> None:
        first = self.record(record_id="usage-1", fingerprint="a" * 64)
        second = self.record(record_id="usage-2", fingerprint="a" * 64)

        self.assertEqual(first, self.repository.append(first))
        self.assertEqual(first, self.repository.append(second))
        self.assertEqual([first], self.repository.list(self.owner_id))

    def test_cursor_pagination_and_invalid_limit(self) -> None:
        records = [self.record(record_id=f"usage-{index}", seconds=index) for index in range(3)]
        for record in records:
            self.repository.append(record)

        page = self.repository.list(self.owner_id, limit=2)
        self.assertEqual([records[2], records[1]], page)
        self.assertEqual([records[0]], self.repository.list(
            self.owner_id, limit=2, before=(page[-1].occurred_at, page[-1].id)
        ))
        with self.assertRaisesRegex(UsageRepositoryError, "limit"):
            self.repository.list(self.owner_id, limit=101)

    def test_corrupt_payload_fails_closed(self) -> None:
        record = self.record(record_id="usage-corrupt")
        self.repository.append(record)
        with sqlite3.connect(self.layout.database_path()) as connection:
            connection.execute(
                "UPDATE usage_records SET encrypted_payload = ? WHERE id = ?",
                (b"corrupt", record.id),
            )

        with self.assertRaises(UsageRepositoryError) as captured:
            self.repository.list(self.owner_id)
        self.assertEqual("usage_record_corrupt", captured.exception.code)
        self.assertNotIn("corrupt", str(captured.exception))


if __name__ == "__main__":
    unittest.main()
