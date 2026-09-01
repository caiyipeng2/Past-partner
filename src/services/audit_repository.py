"""Encrypted, append-only persistence for owner-scoped business audit events."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from src.domain.audit_events import AuditEvent, AuditEventValidationError
from src.services.audit_chain import GENESIS_HASH, event_hash as calculate_audit_event_hash
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import (
    MetadataIntegrityError,
    MetadataStore,
    MetadataStoreError,
    require_metadata_store,
)


class AuditRepositoryError(RuntimeError):
    """Stable audit failure that never includes payload or driver details."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class AuditRepository:
    _RECORD_VERSION = 1
    _AAD_PREFIX = "past-partner/audit-event/v1/"
    _MAX_LIMIT = 100

    def __init__(self, metadata_store: MetadataStore, encryption: AuthenticatedEncryptionService) -> None:
        self.metadata_store = require_metadata_store(metadata_store)
        self.encryption = encryption
        self.metadata_store.migrate()

    def append(self, event: AuditEvent) -> AuditEvent:
        if not isinstance(event, AuditEvent):
            raise TypeError("event must be an AuditEvent")
        envelope = self._encode(event)
        try:
            with self.metadata_store.transaction(immediate=self.metadata_store.backend_name == "sqlite") as connection:
                self._lock_owner(connection, event.owner_id)
                self._verify_connection(connection, event.owner_id)
                latest = connection.execute(
                    "SELECT chain_sequence, event_hash FROM audit_events "
                    "WHERE owner_id = ? ORDER BY chain_sequence DESC LIMIT 1",
                    (event.owner_id,),
                ).fetchone()
                sequence = 1 if latest is None else latest[0] + 1
                previous_hash = GENESIS_HASH if latest is None else latest[1]
                current_hash = calculate_audit_event_hash(
                    previous_hash=previous_hash,
                    event_id=event.id,
                    owner_id=event.owner_id,
                    action=event.action.value,
                    outcome=event.outcome.value,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    occurred_at=event.occurred_at,
                    record_version=self._RECORD_VERSION,
                    encrypted_payload=envelope,
                )
                connection.execute(
                    """
                    INSERT INTO audit_events
                        (id, owner_id, action, outcome, resource_type, resource_id,
                         occurred_at, record_version, encrypted_payload, chain_sequence,
                         previous_hash, event_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.owner_id,
                        event.action.value,
                        event.outcome.value,
                        event.resource_type,
                        event.resource_id,
                        event.occurred_at,
                        self._RECORD_VERSION,
                        envelope,
                        sequence,
                        previous_hash,
                        current_hash,
                    ),
                )
        except MetadataIntegrityError as exc:
            raise AuditRepositoryError("audit_event_exists", "audit event already exists") from exc
        except MetadataStoreError as exc:
            raise AuditRepositoryError("audit_unavailable", "audit records are unavailable") from exc
        return event

    def verify(self, owner_id: str) -> dict[str, object]:
        owner = self._owner(owner_id)
        try:
            with closing(self.metadata_store.connect()) as connection:
                return self._verify_connection(connection, owner)
        except MetadataStoreError as exc:
            raise AuditRepositoryError("audit_unavailable", "audit records are unavailable") from exc

    @classmethod
    def verify_database(
        cls,
        metadata_store: MetadataStore | Path | str,
        owner_id: str | None = None,
    ) -> dict[str, object]:
        store = require_metadata_store(metadata_store)
        store.migrate()
        with closing(store.connect()) as connection:
            if owner_id is not None:
                return cls._verify_connection(connection, cls._owner(owner_id))
            owners = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT owner_id FROM audit_events ORDER BY owner_id"
                ).fetchall()
            ]
            summaries = [cls._verify_connection(connection, owner) for owner in owners]
        return {"owner_count": len(summaries), "owners": summaries}

    def list(
        self,
        owner_id: str,
        *,
        limit: int = 100,
        before: tuple[str, str] | None = None,
    ) -> list[AuditEvent]:
        owner = self._owner(owner_id)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= self._MAX_LIMIT:
            raise AuditRepositoryError("invalid_audit_limit", "audit limit is invalid")
        if before is not None:
            before = self._cursor(before)
        query = (
            "SELECT id, owner_id, action, outcome, resource_type, resource_id, occurred_at, "
            "record_version, encrypted_payload FROM audit_events WHERE owner_id = ?"
        )
        parameters: list[object] = [owner]
        if before is not None:
            query += " AND (occurred_at < ? OR (occurred_at = ? AND id < ?))"
            parameters.extend((before[0], before[0], before[1]))
        query += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        parameters.append(limit)
        try:
            with closing(self.metadata_store.connect()) as connection:
                rows = connection.execute(query, parameters).fetchall()
        except MetadataStoreError as exc:
            raise AuditRepositoryError("audit_unavailable", "audit records are unavailable") from exc
        return [self._decode(row) for row in rows]

    @staticmethod
    def _lock_owner(connection: object, owner_id: str) -> None:
        result = connection.execute("UPDATE local_users SET id = id WHERE id = ?", (owner_id,))
        if getattr(result, "rowcount", 0) != 1:
            raise AuditRepositoryError("invalid_audit_owner", "audit owner is invalid")

    @staticmethod
    def _verify_connection(connection: object, owner_id: str) -> dict[str, object]:
        rows = connection.execute(
            "SELECT id, owner_id, action, outcome, resource_type, resource_id, occurred_at, "
            "record_version, encrypted_payload, chain_sequence, previous_hash, event_hash "
            "FROM audit_events WHERE owner_id = ? ORDER BY chain_sequence ASC, id ASC",
            (owner_id,),
        ).fetchall()
        expected_sequence = 1
        previous_hash = GENESIS_HASH
        for row in rows:
            values = tuple(row)
            if len(values) != 12:
                raise AuditRepositoryError("audit_chain_gap", "audit chain has a gap")
            (
                event_id,
                row_owner_id,
                action,
                outcome,
                resource_type,
                resource_id,
                occurred_at,
                record_version,
                encrypted_payload,
                sequence,
                stored_previous_hash,
                stored_event_hash,
            ) = values
            if (
                row_owner_id != owner_id
                or not isinstance(sequence, int)
                or sequence != expected_sequence
                or not isinstance(stored_previous_hash, str)
                or len(stored_previous_hash) != 64
                or not isinstance(stored_event_hash, str)
                or len(stored_event_hash) != 64
                or not isinstance(encrypted_payload, (bytes, bytearray, memoryview))
            ):
                raise AuditRepositoryError("audit_chain_gap", "audit chain has a gap")
            expected_hash = calculate_audit_event_hash(
                previous_hash=stored_previous_hash,
                event_id=event_id,
                owner_id=row_owner_id,
                action=action,
                outcome=outcome,
                resource_type=resource_type,
                resource_id=resource_id,
                occurred_at=occurred_at,
                record_version=record_version,
                encrypted_payload=encrypted_payload,
            )
            if stored_previous_hash != previous_hash or stored_event_hash != expected_hash:
                raise AuditRepositoryError("audit_chain_mismatch", "audit chain hash mismatch")
            previous_hash = stored_event_hash
            expected_sequence += 1
        return {
            "owner_id": owner_id,
            "event_count": len(rows),
            "head_hash": previous_hash,
        }

    def _encode(self, event: AuditEvent) -> bytes:
        payload = json.dumps(
            event.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return self.encryption.encrypt(payload, self._aad(event.owner_id, event.id))

    def _decode(self, row: Iterable[object]) -> AuditEvent:
        values = tuple(row)
        if len(values) != 9:
            raise AuditRepositoryError("audit_record_corrupt", "audit record is invalid")
        event_id, owner_id, action, outcome, resource_type, resource_id, occurred_at, version, envelope = values
        if version != self._RECORD_VERSION or not isinstance(envelope, bytes):
            raise AuditRepositoryError("audit_record_corrupt", "audit record is invalid")
        if not all(isinstance(value, str) for value in (event_id, owner_id, action, outcome, resource_type, resource_id, occurred_at)):
            raise AuditRepositoryError("audit_record_corrupt", "audit record is invalid")
        try:
            plaintext = self.encryption.decrypt(envelope, self._aad(owner_id, event_id))
            value = json.loads(plaintext.decode("utf-8"))
            event = AuditEvent(**value)
        except (AuthenticationError, InvalidEncryptedPayloadError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, AuditEventValidationError) as exc:
            raise AuditRepositoryError("audit_record_corrupt", "audit record is invalid") from exc
        if (
            event.id != event_id
            or event.owner_id != owner_id
            or event.action.value != action
            or event.outcome.value != outcome
            or event.resource_type != resource_type
            or event.resource_id != resource_id
            or event.occurred_at != occurred_at
        ):
            raise AuditRepositoryError("audit_record_corrupt", "audit record is invalid")
        return event

    @classmethod
    def _aad(cls, owner_id: str, event_id: str) -> bytes:
        return f"{cls._AAD_PREFIX}{owner_id}/{event_id}".encode("utf-8")

    @staticmethod
    def _owner(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise AuditRepositoryError("invalid_audit_owner", "audit owner is invalid")
        return value.strip()

    @staticmethod
    def _cursor(value: object) -> tuple[str, str]:
        if not isinstance(value, tuple) or len(value) != 2:
            raise AuditRepositoryError("invalid_audit_cursor", "audit cursor is invalid")
        timestamp, event_id = value
        if not isinstance(timestamp, str) or not isinstance(event_id, str) or not event_id:
            raise AuditRepositoryError("invalid_audit_cursor", "audit cursor is invalid")
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise AuditRepositoryError("invalid_audit_cursor", "audit cursor is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise AuditRepositoryError("invalid_audit_cursor", "audit cursor is invalid")
        return parsed.astimezone(UTC).isoformat(), event_id
