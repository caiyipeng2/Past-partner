from datetime import UTC, datetime
import unittest

from src.domain.audit_events import (
    AuditAction,
    AuditEvent,
    AuditEventValidationError,
    AuditOutcome,
)


class AuditEventTests(unittest.TestCase):
    def make_event(self, **changes):
        values = {
            "id": "evt-123",
            "owner_id": "owner-1",
            "action": AuditAction.PERSONA_DELETED,
            "outcome": AuditOutcome.SUCCESS,
            "resource_type": "persona",
            "resource_id": "persona-1",
            "occurred_at": "2026-08-20T10:00:00+08:00",
            "metadata": {"deleted_children": 3, "reason_code": "user_requested"},
        }
        values.update(changes)
        return AuditEvent(**values)

    def test_normalizes_timestamp_and_exposes_json_safe_mapping(self) -> None:
        event = self.make_event()

        self.assertEqual("2026-08-20T02:00:00+00:00", event.occurred_at)
        self.assertEqual("persona_deleted", event.to_dict()["action"])
        self.assertEqual("success", event.to_dict()["outcome"])
        self.assertEqual(3, event.to_dict()["metadata"]["deleted_children"])
        with self.assertRaises(TypeError):
            event.metadata["reason_code"] = "changed"

    def test_accepts_every_declared_action(self) -> None:
        for action in AuditAction:
            event = self.make_event(action=action)
            self.assertEqual(action, event.action)

    def test_rejects_unknown_action_and_outcome(self) -> None:
        with self.assertRaisesRegex(AuditEventValidationError, "action"):
            self.make_event(action="persona_read")
        with self.assertRaisesRegex(AuditEventValidationError, "outcome"):
            self.make_event(outcome="failed")

    def test_rejects_nested_or_unapproved_metadata(self) -> None:
        with self.assertRaisesRegex(AuditEventValidationError, "metadata"):
            self.make_event(metadata={"provider_id": {"name": "deepseek"}})
        with self.assertRaisesRegex(AuditEventValidationError, "metadata"):
            self.make_event(metadata={"raw_content": "message"})
        with self.assertRaisesRegex(AuditEventValidationError, "metadata"):
            self.make_event(metadata={"request_path": "C:/secret"})

    def test_rejects_invalid_identifiers_and_naive_timestamp(self) -> None:
        with self.assertRaisesRegex(AuditEventValidationError, "owner_id"):
            self.make_event(owner_id=" ")
        with self.assertRaisesRegex(AuditEventValidationError, "resource_id"):
            self.make_event(resource_id="")
        with self.assertRaisesRegex(AuditEventValidationError, "occurred_at"):
            self.make_event(occurred_at=datetime.now().isoformat())

    def test_requires_utc_aware_datetime_semantics(self) -> None:
        event = self.make_event(occurred_at=datetime(2026, 8, 20, tzinfo=UTC).isoformat())
        self.assertEqual("2026-08-20T00:00:00+00:00", event.occurred_at)


if __name__ == "__main__":
    unittest.main()
