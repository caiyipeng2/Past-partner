from datetime import UTC
import unittest

from src.domain.usage_records import (
    BillingMode,
    UsageRecord,
    UsageRecordValidationError,
    UsageOperation,
    UsageStatus,
)


class UsageRecordTests(unittest.TestCase):
    def record(self, **changes):
        values = {
            "id": "usage-1",
            "owner_id": "owner-1",
            "operation": UsageOperation.CHAT,
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-flash",
            "billing_mode": BillingMode.PLATFORM_BILLED,
            "occurred_at": "2026-08-20T10:00:00+08:00",
            "input_tokens": 12,
            "output_tokens": 8,
            "media_units": 0,
            "currency": "USD",
            "provider_estimated_cost": 0.00012,
            "platform_charge": 0.00012,
            "status": UsageStatus.PRICED,
            "provider_request_fingerprint": "a" * 64,
        }
        values.update(changes)
        return UsageRecord(**values)

    def test_normalizes_timestamp_and_redacts_request_fingerprint(self) -> None:
        record = self.record()

        self.assertEqual("2026-08-20T02:00:00+00:00", record.occurred_at)
        self.assertEqual("chat", record.to_dict()["operation"])
        self.assertNotIn("provider_request_fingerprint", record.to_dict())
        self.assertEqual("priced", record.to_dict()["status"])

    def test_accepts_explicit_unavailable_usage_without_inventing_amount(self) -> None:
        record = self.record(
            input_tokens=None,
            output_tokens=None,
            provider_estimated_cost=None,
            platform_charge=None,
            status=UsageStatus.USAGE_UNAVAILABLE,
        )

        self.assertIsNone(record.provider_estimated_cost)
        self.assertIsNone(record.platform_charge)

    def test_rejects_inconsistent_status_and_amounts(self) -> None:
        with self.assertRaisesRegex(UsageRecordValidationError, "pricing"):
            self.record(status=UsageStatus.PRICING_UNAVAILABLE, platform_charge=0.2)
        with self.assertRaisesRegex(UsageRecordValidationError, "usage"):
            self.record(status=UsageStatus.USAGE_UNAVAILABLE, input_tokens=1)
        with self.assertRaisesRegex(UsageRecordValidationError, "currency"):
            self.record(currency=None)

    def test_rejects_invalid_identifiers_and_fingerprint(self) -> None:
        with self.assertRaisesRegex(UsageRecordValidationError, "owner_id"):
            self.record(owner_id=" ")
        with self.assertRaisesRegex(UsageRecordValidationError, "fingerprint"):
            self.record(provider_request_fingerprint="not-a-hash")
        with self.assertRaisesRegex(UsageRecordValidationError, "occurred_at"):
            self.record(occurred_at="2026-08-20T10:00:00")

    def test_storage_mapping_round_trips_private_fingerprint(self) -> None:
        record = self.record()
        restored = UsageRecord.from_storage_dict(record.to_storage_dict())

        self.assertEqual(record, restored)
        self.assertEqual(UTC, restored.occurred_at_datetime.tzinfo)


if __name__ == "__main__":
    unittest.main()
