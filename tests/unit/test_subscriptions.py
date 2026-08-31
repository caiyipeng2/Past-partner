from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import shutil
import sqlite3
import unittest
from pathlib import Path

from src.domain.subscriptions import (
    SubscriptionEvent,
    SubscriptionStatus,
    SubscriptionValidationError,
)
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.local_auth import LocalAuthService
from src.services.master_key import EnvironmentMasterKeyProvider, MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.storage import StorageLayout
from src.services.subscription_repository import SubscriptionRepository, SubscriptionRepositoryError
from src.services.subscription_service import SubscriptionService, SubscriptionServiceError
from uuid import uuid4


class SubscriptionDomainTests(unittest.TestCase):
    def event(self, **changes) -> SubscriptionEvent:
        values = {
            "id": "event-1",
            "owner_id": "owner-1",
            "provider_id": "stripe",
            "provider_event_key": "evt-1",
            "provider_subscription_id": "sub-1",
            "plan_id": "plus-monthly",
            "status": SubscriptionStatus.ACTIVE,
            "current_period_start": "2026-08-01T00:00:00+00:00",
            "current_period_end": "2026-09-01T00:00:00+00:00",
            "occurred_at": "2026-08-01T00:00:00+00:00",
        }
        values.update(changes)
        return SubscriptionEvent(**values)

    def test_event_validates_status_period_and_hides_provider_event_key(self) -> None:
        event = self.event(status="trial")

        self.assertEqual(SubscriptionStatus.TRIAL, event.status)
        self.assertNotIn("provider_event_key", event.to_public_dict())
        self.assertEqual("plus-monthly", event.to_dict()["plan_id"])

        with self.assertRaises(SubscriptionValidationError):
            self.event(
                current_period_end="2026-08-01T00:00:00+00:00",
            )

    def test_event_rejects_naive_dates_and_unknown_status(self) -> None:
        for changes in (
            {"status": "unknown"},
            {"current_period_start": "2026-08-01T00:00:00"},
            {"occurred_at": "2026-08-01T00:00:00"},
            {"provider_id": "p" * 129},
        ):
            with self.subTest(changes=changes), self.assertRaises(SubscriptionValidationError):
                self.event(**changes)


class SubscriptionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"s" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(layout.database_path(), self.encryption, mode="test")
        self.repository = SubscriptionRepository(self.auth.metadata_store, self.encryption)
        self.owner_id = self.auth.owner_id

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def event(self, **changes) -> SubscriptionEvent:
        values = {
            "id": "event-1",
            "owner_id": self.owner_id,
            "provider_id": "stripe",
            "provider_event_key": "evt-1",
            "provider_subscription_id": "sub-1",
            "plan_id": "plus-monthly",
            "status": SubscriptionStatus.ACTIVE,
            "current_period_start": "2026-08-01T00:00:00+00:00",
            "current_period_end": "2026-09-01T00:00:00+00:00",
            "occurred_at": "2026-08-01T00:00:00+00:00",
        }
        values.update(changes)
        return SubscriptionEvent(**values)

    def test_verified_event_is_encrypted_owner_scoped_and_replayed_idempotently(self) -> None:
        event = self.event()
        subscription = self.repository.apply(event)
        self.assertEqual("sub-1", subscription.provider_subscription_id)
        self.assertEqual("plus-monthly", subscription.plan_id)
        self.assertEqual(SubscriptionStatus.ACTIVE, subscription.status)
        self.assertEqual(self.repository.apply(event), self.repository.apply(self.event(id="event-retry")))
        self.assertEqual([], self.repository.list_events("other-owner"))
        with sqlite3.connect(StorageLayout(self.root).database_path()) as connection:
            subscription_payload = connection.execute(
                "SELECT encrypted_payload FROM subscriptions WHERE owner_id = ?", (self.owner_id,)
            ).fetchone()[0]
            event_payload = connection.execute(
                "SELECT encrypted_payload FROM subscription_events WHERE owner_id = ?", (self.owner_id,)
            ).fetchone()[0]
        self.assertNotIn(b"plus-monthly", subscription_payload)
        self.assertNotIn(b"evt-1", event_payload)

    def test_conflicting_event_key_and_stale_event_are_handled_without_regression(self) -> None:
        self.repository.apply(self.event())
        with self.assertRaises(SubscriptionRepositoryError) as captured:
            self.repository.apply(self.event(plan_id="pro-monthly"))
        self.assertEqual("subscription_event_conflict", captured.exception.code)

        stale = self.event(
            id="event-old",
            provider_event_key="evt-old",
            plan_id="legacy",
            occurred_at="2026-07-01T00:00:00+00:00",
        )
        current = self.repository.apply(stale)
        self.assertEqual("plus-monthly", current.plan_id)

    def test_provider_event_and_subscription_identity_are_global_across_owners(self) -> None:
        self.repository.apply(self.event())
        second_owner = self.auth.create_local_account("second-owner")["user_id"]

        with self.assertRaises(SubscriptionRepositoryError) as captured:
            self.repository.apply(self.event(owner_id=second_owner))
        self.assertEqual("subscription_event_conflict", captured.exception.code)

        with self.assertRaises(SubscriptionRepositoryError) as captured:
            self.repository.apply(
                self.event(
                    owner_id=second_owner,
                    provider_event_key="evt-2",
                    id="event-2",
                )
            )
        self.assertEqual("subscription_identity_conflict", captured.exception.code)

    def test_equal_timestamp_state_conflict_is_rejected(self) -> None:
        self.repository.apply(self.event())
        with self.assertRaises(SubscriptionRepositoryError) as captured:
            self.repository.apply(
                self.event(
                    provider_event_key="evt-cancel",
                    id="event-cancel",
                    status=SubscriptionStatus.CANCELLED,
                )
            )
        self.assertEqual("subscription_timestamp_conflict", captured.exception.code)
        self.assertEqual(SubscriptionStatus.ACTIVE, self.repository.get(self.owner_id).status)

    def test_cancelled_subscription_can_switch_identity_on_newer_event(self) -> None:
        self.repository.apply(self.event())
        self.repository.apply(
            self.event(
                provider_event_key="evt-cancel",
                id="event-cancel",
                status=SubscriptionStatus.CANCELLED,
                occurred_at="2026-08-10T00:00:00+00:00",
            )
        )
        current = self.repository.apply(
            self.event(
                provider_event_key="evt-new-subscription",
                id="event-new-subscription",
                provider_subscription_id="sub-2",
                occurred_at="2026-08-11T00:00:00+00:00",
            )
        )
        self.assertEqual("sub-2", current.provider_subscription_id)
        self.assertEqual(SubscriptionStatus.ACTIVE, current.status)

    def test_missing_owner_subscription_is_explicit(self) -> None:
        self.assertIsNone(self.repository.get(self.owner_id))
        with self.assertRaises(SubscriptionRepositoryError) as captured:
            self.repository.apply(self.event(owner_id="missing-owner"))
        self.assertEqual("subscription_owner_invalid", captured.exception.code)


class SubscriptionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"t" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(layout.database_path(), self.encryption, mode="test")
        self.repository = SubscriptionRepository(self.auth.metadata_store, self.encryption)
        self.service = SubscriptionService(self.repository)
        self.owner_id = self.auth.owner_id

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_unverified_provider_event_is_rejected_before_persistence(self) -> None:
        with self.assertRaises(SubscriptionServiceError) as captured:
            self.service.apply_provider_event(
                self.owner_id,
                provider_id="stripe",
                provider_event_key="evt-1",
                provider_subscription_id="sub-1",
                plan_id="plus-monthly",
                status="active",
                current_period_start="2026-08-01T00:00:00+00:00",
                current_period_end="2026-09-01T00:00:00+00:00",
                occurred_at="2026-08-01T00:00:00+00:00",
                signature_verified=False,
            )
        self.assertEqual("subscription_event_unverified", captured.exception.code)
        self.assertIsNone(self.repository.get(self.owner_id))

    def test_current_entitlement_is_bounded_and_cancelled_is_not_entitled(self) -> None:
        self.service.apply_provider_event(
            self.owner_id,
            provider_id="stripe",
            provider_event_key="evt-1",
            provider_subscription_id="sub-1",
            plan_id="plus-monthly",
            status="active",
            current_period_start="2026-08-01T00:00:00+00:00",
            current_period_end="2026-09-01T00:00:00+00:00",
            occurred_at="2026-08-01T00:00:00+00:00",
            signature_verified=True,
        )
        current = self.service.current(
            self.owner_id,
            now=datetime(2026, 8, 15, tzinfo=UTC),
        )
        self.assertTrue(current["entitled"])
        self.assertEqual("active", current["subscription"]["status"])
        self.assertNotIn("provider_event_key", current["subscription"])

        self.service.apply_provider_event(
            self.owner_id,
            provider_id="stripe",
            provider_event_key="evt-2",
            provider_subscription_id="sub-1",
            plan_id="plus-monthly",
            status="cancelled",
            current_period_start="2026-08-01T00:00:00+00:00",
            current_period_end="2026-09-01T00:00:00+00:00",
            occurred_at="2026-08-16T00:00:00+00:00",
            signature_verified=True,
        )
        cancelled = self.service.current(
            self.owner_id,
            now=datetime(2026, 8, 16, tzinfo=UTC),
        )
        self.assertFalse(cancelled["entitled"])
        self.assertEqual("cancelled", cancelled["subscription"]["status"])

        empty = self.service.current("other-owner", now=datetime(2026, 8, 16, tzinfo=UTC))
        self.assertEqual({"subscription": None, "entitled": False}, empty)

    def test_past_due_is_entitled_only_inside_the_current_period(self) -> None:
        self.service.apply_provider_event(
            self.owner_id,
            provider_id="stripe",
            provider_event_key="evt-past-due",
            provider_subscription_id="sub-1",
            plan_id="plus-monthly",
            status="past_due",
            current_period_start="2026-08-01T00:00:00+00:00",
            current_period_end="2026-09-01T00:00:00+00:00",
            occurred_at="2026-08-20T00:00:00+00:00",
            signature_verified=True,
        )

        self.assertTrue(self.service.current(self.owner_id, now=datetime(2026, 8, 20, tzinfo=UTC))["entitled"])
        self.assertFalse(self.service.current(self.owner_id, now=datetime(2026, 9, 1, tzinfo=UTC))["entitled"])


if __name__ == "__main__":
    unittest.main()
