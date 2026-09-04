"""Role-gated, redacted operational health aggregation."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
from typing import Sequence

from src.services.audit_repository import AuditRepository, AuditRepositoryError
from src.services.local_auth import OwnerPrincipal
from src.services.metadata_store import MetadataStore, MetadataStoreError, require_metadata_store


class OperationsSummaryError(RuntimeError):
    """Stable operations error without owner data or driver details."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class OperationsSummaryService:
    """Aggregate fixed operational counters without exposing business records."""

    _MAX_DIAGNOSTIC_IDS = 20
    _MAX_AUDIT_OWNERS = 100
    _MAX_AUDIT_EVENTS_PER_OWNER = 100
    _EXPECTED_COUNTS = {
        ("task_queue", "state"): ("queued", "leased", "succeeded", "failed", "cancelled"),
        ("data_subject_notifications", "status"): ("pending", "delivered", "failed"),
        (
            "worker_observations",
            "outcome",
        ): ("idle", "succeeded", "retryable_failure", "terminal_failure", "lease_lost"),
    }

    def __init__(self, metadata_store: MetadataStore, audit_repository: AuditRepository | None):
        self.metadata_store = require_metadata_store(metadata_store)
        self.audit_repository = audit_repository

    def summarize(
        self,
        principal: OwnerPrincipal,
        *,
        diagnostic_ids: Sequence[str] = (),
    ) -> dict[str, object]:
        if not isinstance(principal, OwnerPrincipal) or principal.role != "admin":
            raise OperationsSummaryError(
                "operations_admin_required", "administrator role is required"
            )
        try:
            with closing(self.metadata_store.connect()) as connection:
                queue_states = self._counts(connection, "task_queue", "state")
                outbox_states = {
                    "pending": self._scalar(
                        connection,
                        "SELECT COUNT(*) FROM task_broker_outbox WHERE published_at IS NULL",
                    ),
                    "published": self._scalar(
                        connection,
                        "SELECT COUNT(*) FROM task_broker_outbox WHERE published_at IS NOT NULL",
                    ),
                }
                billing = {
                    "accounts": self._scalar(connection, "SELECT COUNT(*) FROM billing_accounts"),
                    "entries": self._scalar(connection, "SELECT COUNT(*) FROM billing_entries"),
                    "status": "available",
                    "reconciliation": "local_ledger_only",
                }
                notifications = self._counts(connection, "data_subject_notifications", "status")
                worker_outcomes = self._counts(connection, "worker_observations", "outcome")
        except MetadataStoreError as exc:
            raise OperationsSummaryError(
                "operations_unavailable", "operations summary is unavailable"
            ) from exc

        try:
            audit = self._audit_summary()
        except MetadataStoreError as exc:
            raise OperationsSummaryError(
                "operations_unavailable", "operations summary is unavailable"
            ) from exc
        safe_diagnostics = tuple(
            value for value in diagnostic_ids[-self._MAX_DIAGNOSTIC_IDS :] if _is_uuid_text(value)
        )
        return {
            "status": "ok",
            "generated_at": datetime.now(UTC).isoformat(),
            "access": {"role": "admin", "scope": "operations:read"},
            "queue": {
                "states": queue_states,
                "outbox": outbox_states,
            },
            "billing": billing,
            "audit": audit,
            "notifications": {
                "states": notifications,
                "failed": notifications.get("failed", 0),
            },
            "workers": {"outcomes": worker_outcomes},
            "diagnostic_ids": list(safe_diagnostics),
        }

    def _audit_summary(self) -> dict[str, object]:
        if self.audit_repository is None:
            return {"status": "unavailable", "event_count": 0, "error_code": "audit_unavailable"}
        try:
            with closing(self.metadata_store.connect()) as connection:
                owner_rows = connection.execute(
                    "SELECT DISTINCT owner_id FROM audit_events ORDER BY owner_id LIMIT ?",
                    (self._MAX_AUDIT_OWNERS + 1,),
                ).fetchall()
                complete = len(owner_rows) <= self._MAX_AUDIT_OWNERS
                event_count = 0
                checked_owners = 0
                checked_events = 0
                for row in owner_rows[: self._MAX_AUDIT_OWNERS]:
                    if not isinstance(row, (tuple, list)) or len(row) != 1:
                        return {
                            "status": "unavailable",
                            "event_count": 0,
                            "error_code": "audit_record_corrupt",
                        }
                    owner_id = AuditRepository._owner(row[0])
                    event_rows = connection.execute(
                        "SELECT id, owner_id, action, outcome, resource_type, resource_id, occurred_at, "
                        "record_version, encrypted_payload, chain_sequence, previous_hash, event_hash "
                        "FROM audit_events WHERE owner_id = ? ORDER BY chain_sequence ASC, id ASC LIMIT ?",
                        (owner_id, self._MAX_AUDIT_EVENTS_PER_OWNER + 1),
                    ).fetchall()
                    owner_complete = len(event_rows) <= self._MAX_AUDIT_EVENTS_PER_OWNER
                    complete = complete and owner_complete
                    bounded_rows = event_rows[: self._MAX_AUDIT_EVENTS_PER_OWNER]
                    verified = AuditRepository._verify_rows(bounded_rows, owner_id)
                    event_count += int(verified["event_count"])
                    checked_owners += 1
                    checked_events += len(bounded_rows)
                return {
                    "status": "ok" if complete else "partial",
                    "event_count": event_count,
                    "complete": complete,
                    "checked_owners": checked_owners,
                    "checked_events": checked_events,
                }
        except AuditRepositoryError as exc:
            return {"status": "unavailable", "event_count": 0, "error_code": exc.code}
        except MetadataStoreError as exc:
            raise OperationsSummaryError(
                "operations_unavailable", "operations summary is unavailable"
            ) from exc

    @staticmethod
    def _scalar(connection: object, query: str) -> int:
        row = connection.execute(query).fetchone()
        value = row[0] if row else 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise OperationsSummaryError("operations_record_corrupt", "operations summary is invalid")
        return value

    @classmethod
    def _counts(cls, connection: object, table: str, column: str) -> dict[str, int]:
        expected = cls._EXPECTED_COUNTS.get((table, column))
        if expected is None:
            raise OperationsSummaryError("operations_record_corrupt", "operations summary is invalid")
        rows = connection.execute(
            f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column}"
        ).fetchall()
        result = {key: 0 for key in expected}
        for key, count in rows:
            if (
                not isinstance(key, str)
                or key not in result
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                raise OperationsSummaryError("operations_record_corrupt", "operations summary is invalid")
            result[key] = count
        return result


def _is_uuid_text(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 36:
        return False
    try:
        from uuid import UUID

        UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


__all__ = ["OperationsSummaryError", "OperationsSummaryService"]
