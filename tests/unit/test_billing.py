from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.domain.billing import (
    BillingDirection,
    BillingEntry,
    BillingEntryValidationError,
    BillingSource,
)
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.billing_repository import BillingRepository, BillingRepositoryError
from src.services.billing_service import BillingService, BillingServiceError
from src.services.local_auth import LocalAuthService
from src.services.master_key import EnvironmentMasterKeyProvider, MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.storage import StorageLayout


class BillingDomainTests(unittest.TestCase):
    def test_entry_uses_minor_units_and_public_view_excludes_idempotency_key(self) -> None:
        entry = BillingEntry(
            id="billing-1",
            owner_id="owner-1",
            direction=BillingDirection.CREDIT,
            currency="CNY",
            amount_minor=1234,
            source=BillingSource.PAYMENT,
            operation_key="payment-1",
            occurred_at="2026-08-31T10:00:00+00:00",
        )

        self.assertEqual(1234, entry.amount_minor)
        self.assertEqual("credit", entry.to_dict()["direction"])
        self.assertNotIn("operation_key", entry.to_public_dict())
        with self.assertRaises(BillingEntryValidationError):
            BillingEntry(
                id="billing-2",
                owner_id="owner-1",
                direction=BillingDirection.DEBIT,
                currency="cny",
                amount_minor=1,
                source=BillingSource.USAGE,
                operation_key="usage-1",
                occurred_at="2026-08-31T10:00:00+00:00",
            )

    def test_entry_rejects_zero_negative_and_fractional_amounts(self) -> None:
        for value in (0, -1, 1.5, True):
            with self.subTest(value=value), self.assertRaises(BillingEntryValidationError):
                BillingEntry(
                    id="billing-1",
                    owner_id="owner-1",
                    direction=BillingDirection.CREDIT,
                    currency="CNY",
                    amount_minor=value,
                    source=BillingSource.PAYMENT,
                    operation_key="payment-1",
                    occurred_at="2026-08-31T10:00:00+00:00",
                )


class BillingRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"b" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(layout.database_path(), self.encryption, mode="test")
        self.repository = BillingRepository(self.auth.metadata_store, self.encryption)
        self.owner_id = self.auth.owner_id

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def entry(
        self,
        *,
        entry_id: str,
        direction: BillingDirection,
        amount: int,
        key: str,
        seconds: int = 0,
        currency: str = "CNY",
    ) -> BillingEntry:
        return BillingEntry(
            id=entry_id,
            owner_id=self.owner_id,
            direction=direction,
            currency=currency,
            amount_minor=amount,
            source=BillingSource.PAYMENT if direction is BillingDirection.CREDIT else BillingSource.USAGE,
            operation_key=key,
            occurred_at=(datetime(2026, 8, 31, 10, 0, tzinfo=UTC) + timedelta(seconds=seconds)).isoformat(),
        )

    def test_append_is_encrypted_owner_scoped_and_balance_is_integrity_checked(self) -> None:
        credit = self.entry(entry_id="billing-1", direction=BillingDirection.CREDIT, amount=1000, key="payment-1")
        debit = self.entry(entry_id="billing-2", direction=BillingDirection.DEBIT, amount=250, key="usage-1", seconds=1)

        self.repository.append(credit)
        self.repository.append(debit)

        self.assertEqual(750, self.repository.balance(self.owner_id, "CNY"))
        self.assertEqual([debit, credit], self.repository.list(self.owner_id))
        self.assertEqual([], self.repository.list("other-owner"))
        with sqlite3.connect(StorageLayout(self.root).database_path()) as connection:
            payloads = [row[0] for row in connection.execute("SELECT encrypted_payload FROM billing_entries")]
            operation_hash = connection.execute(
                "SELECT operation_key_hash FROM billing_entries WHERE id = ?", (credit.id,)
            ).fetchone()[0]
        self.assertTrue(payloads)
        self.assertTrue(all(b"payment-1" not in payload for payload in payloads))
        self.assertEqual(sha256(b"payment-1").hexdigest(), operation_hash)

    def test_duplicate_operation_key_is_idempotent_but_conflicting_payload_fails(self) -> None:
        first = self.entry(entry_id="billing-1", direction=BillingDirection.CREDIT, amount=1000, key="payment-1")
        same_key = self.entry(entry_id="billing-2", direction=BillingDirection.CREDIT, amount=1000, key="payment-1")
        different = self.entry(entry_id="billing-3", direction=BillingDirection.CREDIT, amount=2000, key="payment-1")

        self.assertEqual(first, self.repository.append(first))
        self.assertEqual(first, self.repository.append(same_key))
        with self.assertRaises(BillingRepositoryError) as captured:
            self.repository.append(different)
        self.assertEqual("billing_idempotency_conflict", captured.exception.code)

    def test_debit_rejects_insufficient_balance_without_writing(self) -> None:
        debit = self.entry(entry_id="billing-1", direction=BillingDirection.DEBIT, amount=1, key="usage-1")

        with self.assertRaises(BillingRepositoryError) as captured:
            self.repository.append(debit, enforce_balance=True)
        self.assertEqual("billing_insufficient_balance", captured.exception.code)
        self.assertEqual([], self.repository.list(self.owner_id))

    def test_currency_is_fixed_per_owner_account(self) -> None:
        self.repository.append(self.entry(entry_id="billing-1", direction=BillingDirection.CREDIT, amount=100, key="payment-1"))
        eur = self.entry(
            entry_id="billing-2",
            direction=BillingDirection.CREDIT,
            amount=100,
            key="payment-2",
            currency="EUR",
        )

        with self.assertRaises(BillingRepositoryError) as captured:
            self.repository.append(eur)
        self.assertEqual("billing_currency_mismatch", captured.exception.code)

    def test_concurrent_same_operation_key_commits_once(self) -> None:
        entries = [
            self.entry(entry_id=f"billing-{index}", direction=BillingDirection.CREDIT, amount=100, key="payment-concurrent")
            for index in (1, 2)
        ]

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(self.repository.append, entries))

        self.assertEqual(results[0], results[1])
        self.assertEqual([results[0]], self.repository.list(self.owner_id))


class BillingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"c" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(layout.database_path(), self.encryption, mode="test")
        self.repository = BillingRepository(self.auth.metadata_store, self.encryption)
        self.service = BillingService(self.repository)
        self.owner_id = self.auth.owner_id

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_credit_and_debit_are_idempotent_and_balance_is_minor_units(self) -> None:
        credit = self.service.credit(
            self.owner_id,
            amount_minor=1000,
            currency="CNY",
            operation_key="payment-1",
        )
        self.assertEqual(1000, self.service.balance(self.owner_id, "CNY")["balance_minor"])
        self.assertEqual(credit, self.service.credit(
            self.owner_id,
            amount_minor=1000,
            currency="CNY",
            operation_key="payment-1",
        ))
        self.service.debit(
            self.owner_id,
            amount_minor=250,
            currency="CNY",
            operation_key="usage-1",
        )
        self.assertEqual(750, self.service.balance(self.owner_id, "CNY")["balance_minor"])

    def test_invalid_source_and_currency_are_stable_errors(self) -> None:
        with self.assertRaises(BillingServiceError) as captured:
            self.service.credit(
                self.owner_id,
                amount_minor=100,
                currency="EUR",
                operation_key="payment-1",
                source="usage",
            )
        self.assertEqual("billing_source_invalid", captured.exception.code)

        with self.assertRaises(BillingServiceError) as captured:
            self.service.balance(self.owner_id, "cny")
        self.assertEqual("billing_currency_invalid", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
