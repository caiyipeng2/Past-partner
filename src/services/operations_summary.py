"""Role-gated, redacted operational health aggregation."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import UTC, datetime
from typing import Iterable, Sequence

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

        audit = self._audit_summary()
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
            verified = AuditRepository.verify_database(self.metadata_store)
        except AuditRepositoryError as exc:
            return {"status": "unavailable", "event_count": 0, "error_code": exc.code}
        owners = verified.get("owners", [])
        if not isinstance(owners, list):
            return {"status": "unavailable", "event_count": 0, "error_code": "audit_record_corrupt"}
        event_count = sum(
            int(item.get("event_count", 0))
            for item in owners
            if isinstance(item, dict) and isinstance(item.get("event_count", 0), int)
        )
        return {"status": "ok", "event_count": event_count}

    @staticmethod
    def _scalar(connection: object, query: str) -> int:
        row = connection.execute(query).fetchone()
        value = row[0] if row else 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise OperationsSummaryError("operations_record_corrupt", "operations summary is invalid")
        return value

    @classmethod
    def _counts(cls, connection: object, table: str, column: str) -> dict[str, int]:
        allowed = {
            ("task_queue", "state"),
            ("data_subject_notifications", "status"),
            ("worker_observations", "outcome"),
        }
        if (table, column) not in allowed:
            raise OperationsSummaryError("operations_record_corrupt", "operations summary is invalid")
        rows = connection.execute(
            f"SELECT {column}, COUNT(*) FROM {table} GROUP BY {column}"
        ).fetchall()
        result: Counter[str] = Counter()
        for key, count in rows:
            if not isinstance(key, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise OperationsSummaryError("operations_record_corrupt", "operations summary is invalid")
            result[key] = count
        return dict(sorted(result.items()))


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
