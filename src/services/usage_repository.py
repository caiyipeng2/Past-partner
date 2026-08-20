"""Encrypted append-only owner-scoped usage ledger persistence."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
import json

from src.domain.usage_records import UsageRecord, UsageRecordValidationError
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import MetadataIntegrityError, MetadataStore, require_metadata_store


class UsageRepositoryError(RuntimeError):
    """Stable usage ledger error that never includes payload or driver details."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class UsageRepository:
    _RECORD_VERSION = 1
    _AAD_PREFIX = "past-partner/usage/v1/"
    _MAX_LIMIT = 100

    def __init__(self, metadata_store: MetadataStore, encryption: AuthenticatedEncryptionService) -> None:
        self.metadata_store = require_metadata_store(metadata_store)
        self.encryption = encryption
        self.metadata_store.migrate()

    def append(self, record: UsageRecord) -> UsageRecord:
        if not isinstance(record, UsageRecord):
            raise TypeError("record must be a UsageRecord")
        envelope = self._encode(record)
        try:
            with self.metadata_store.transaction(immediate=self.metadata_store.backend_name == "sqlite") as connection:
                if record.provider_request_fingerprint is not None:
                    existing = connection.execute(
                        "SELECT id, owner_id, operation, provider_id, model_id, billing_mode, "
                        "charge_state, occurred_at, provider_request_fingerprint, record_version, encrypted_payload "
                        "FROM usage_records WHERE owner_id = ? AND provider_id = ? "
                        "AND provider_request_fingerprint = ?",
                        (record.owner_id, record.provider_id, record.provider_request_fingerprint),
                    ).fetchone()
                    if existing is not None:
                        return self._decode(existing)
                connection.execute(
                    """
                    INSERT INTO usage_records
                        (id, owner_id, operation, provider_id, model_id, billing_mode,
                         charge_state, occurred_at, provider_request_fingerprint,
                         record_version, encrypted_payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.owner_id,
                        record.operation.value,
                        record.provider_id,
                        record.model_id,
                        record.billing_mode.value,
                        record.status.value,
                        record.occurred_at,
                        record.provider_request_fingerprint,
                        self._RECORD_VERSION,
                        envelope,
                    ),
                )
        except MetadataIntegrityError as exc:
            raise UsageRepositoryError("usage_record_exists", "usage record already exists") from exc
        return record

    def list(
        self,
        owner_id: str,
        *,
        limit: int = 100,
        before: tuple[str, str] | None = None,
    ) -> list[UsageRecord]:
        owner = self._owner(owner_id)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= self._MAX_LIMIT:
            raise UsageRepositoryError("invalid_usage_limit", "usage limit is invalid")
        if before is not None:
            before = self._cursor(before)
        query = (
            "SELECT id, owner_id, operation, provider_id, model_id, billing_mode, charge_state, "
            "occurred_at, provider_request_fingerprint, record_version, encrypted_payload FROM usage_records "
            "WHERE owner_id = ?"
        )
        parameters: list[object] = [owner]
        if before is not None:
            query += " AND (occurred_at < ? OR (occurred_at = ? AND id < ?))"
            parameters.extend((before[0], before[0], before[1]))
        query += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        parameters.append(limit)
        with closing(self.metadata_store.connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._decode(row) for row in rows]

    def _encode(self, record: UsageRecord) -> bytes:
        payload = json.dumps(
            record.to_storage_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return self.encryption.encrypt(payload, self._aad(record.owner_id, record.id))

    def _decode(self, row: Iterable[object]) -> UsageRecord:
        values = tuple(row)
        if len(values) != 11:
            raise UsageRepositoryError("usage_record_corrupt", "usage record is invalid")
        (
            record_id,
            owner_id,
            operation,
            provider_id,
            model_id,
            billing_mode,
            charge_state,
            occurred_at,
            fingerprint,
            version,
            envelope,
        ) = values
        if version != self._RECORD_VERSION or not isinstance(envelope, bytes):
            raise UsageRepositoryError("usage_record_corrupt", "usage record is invalid")
        if not isinstance(record_id, str) or not isinstance(owner_id, str):
            raise UsageRepositoryError("usage_record_corrupt", "usage record is invalid")
        try:
            plaintext = self.encryption.decrypt(envelope, self._aad(owner_id, record_id))
            value = json.loads(plaintext.decode("utf-8"))
            record = UsageRecord.from_storage_dict(value)
        except (
            AuthenticationError,
            InvalidEncryptedPayloadError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            UsageRecordValidationError,
        ) as exc:
            raise UsageRepositoryError("usage_record_corrupt", "usage record is invalid") from exc
        if (
            record.id != record_id
            or record.owner_id != owner_id
            or record.operation.value != operation
            or record.provider_id != provider_id
            or record.model_id != model_id
            or record.billing_mode.value != billing_mode
            or record.status.value != charge_state
            or record.occurred_at != occurred_at
            or record.provider_request_fingerprint != fingerprint
        ):
            raise UsageRepositoryError("usage_record_corrupt", "usage record is invalid")
        return record

    @classmethod
    def _aad(cls, owner_id: str, record_id: str) -> bytes:
        return f"{cls._AAD_PREFIX}{owner_id}/{record_id}".encode("utf-8")

    @staticmethod
    def _owner(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise UsageRepositoryError("invalid_usage_owner", "usage owner is invalid")
        return value.strip()

    @staticmethod
    def _cursor(value: object) -> tuple[str, str]:
        if not isinstance(value, tuple) or len(value) != 2:
            raise UsageRepositoryError("invalid_usage_cursor", "usage cursor is invalid")
        timestamp, record_id = value
        if not isinstance(timestamp, str) or not isinstance(record_id, str) or not record_id:
            raise UsageRepositoryError("invalid_usage_cursor", "usage cursor is invalid")
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise UsageRepositoryError("invalid_usage_cursor", "usage cursor is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise UsageRepositoryError("invalid_usage_cursor", "usage cursor is invalid")
        return parsed.astimezone(UTC).isoformat(), record_id
