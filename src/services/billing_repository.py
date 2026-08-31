"""Encrypted owner-scoped append-only billing ledger persistence."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
import json

from src.domain.billing import BillingDirection, BillingEntry, BillingEntryValidationError
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import MetadataIntegrityError, MetadataStore, require_metadata_store


class BillingRepositoryError(RuntimeError):
    """Stable billing failure without payload or database details."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class BillingRepository:
    _RECORD_VERSION = 1
    _AAD_PREFIX = "past-partner/billing-entry/v1/"
    _MAX_LIMIT = 100

    def __init__(self, metadata_store: MetadataStore, encryption: AuthenticatedEncryptionService) -> None:
        self.metadata_store = require_metadata_store(metadata_store)
        self.encryption = encryption
        self.metadata_store.migrate()

    def append(self, entry: BillingEntry, *, enforce_balance: bool = False) -> BillingEntry:
        if not isinstance(entry, BillingEntry):
            raise TypeError("entry must be a BillingEntry")
        envelope = self._encode(entry)
        operation_key_hash = self._operation_key_hash(entry.operation_key)
        try:
            with self.metadata_store.transaction(immediate=self.metadata_store.backend_name == "sqlite") as connection:
                existing_row = self._find_operation(connection, entry.owner_id, operation_key_hash)
                if existing_row is not None:
                    existing = self._decode(existing_row)
                    if self._same_operation(existing, entry):
                        return existing
                    raise BillingRepositoryError("billing_idempotency_conflict", "billing operation key conflicts")
                # The account row is both the single-currency authority and a
                # per-owner lock. UPDATE remains a real row lock on PostgreSQL,
                # while SQLite's immediate transaction keeps the same boundary.
                connection.execute(
                    "INSERT INTO billing_accounts (owner_id, currency) VALUES (?, ?) "
                    "ON CONFLICT(owner_id) DO NOTHING",
                    (entry.owner_id, entry.currency),
                )
                connection.execute(
                    "UPDATE billing_accounts SET currency = currency WHERE owner_id = ?",
                    (entry.owner_id,),
                )
                account_row = connection.execute(
                    "SELECT currency FROM billing_accounts WHERE owner_id = ?",
                    (entry.owner_id,),
                ).fetchone()
                if account_row is None or account_row[0] != entry.currency:
                    raise BillingRepositoryError(
                        "billing_currency_mismatch",
                        "billing currency does not match the account",
                    )
                # A concurrent PostgreSQL request can have waited on the
                # account-row lock after the first check; re-read the unique
                # operation key before calculating a debit or inserting.
                existing_row = self._find_operation(connection, entry.owner_id, operation_key_hash)
                if existing_row is not None:
                    existing = self._decode(existing_row)
                    if self._same_operation(existing, entry):
                        return existing
                    raise BillingRepositoryError("billing_idempotency_conflict", "billing operation key conflicts")
                if enforce_balance and entry.direction is BillingDirection.DEBIT:
                    balance = self._balance_in_transaction(connection, entry.owner_id, entry.currency)
                    if balance < entry.amount_minor:
                        raise BillingRepositoryError("billing_insufficient_balance", "billing balance is insufficient")
                connection.execute(
                    """
                    INSERT INTO billing_entries
                        (id, owner_id, direction, currency, amount_minor, source, operation_key_hash,
                         occurred_at, record_version, encrypted_payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.owner_id,
                        entry.direction.value,
                        entry.currency,
                        entry.amount_minor,
                        entry.source.value,
                        operation_key_hash,
                        entry.occurred_at,
                        self._RECORD_VERSION,
                        envelope,
                    ),
                )
        except BillingRepositoryError:
            raise
        except MetadataIntegrityError as exc:
            raise BillingRepositoryError("billing_entry_exists", "billing entry already exists") from exc
        return entry

    def list(
        self,
        owner_id: str,
        *,
        limit: int = 100,
        before: tuple[str, str] | None = None,
    ) -> list[BillingEntry]:
        owner = self._owner(owner_id)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= self._MAX_LIMIT:
            raise BillingRepositoryError("invalid_billing_limit", "billing limit is invalid")
        if before is not None:
            before = self._cursor(before)
        query = (
            "SELECT id, owner_id, direction, currency, amount_minor, source, operation_key_hash, occurred_at, "
            "record_version, encrypted_payload FROM billing_entries WHERE owner_id = ?"
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

    def balance(self, owner_id: str, currency: str) -> int:
        owner = self._owner(owner_id)
        self._currency(currency)
        with closing(self.metadata_store.connect()) as connection:
            rows = connection.execute(
                "SELECT id, owner_id, direction, currency, amount_minor, source, operation_key_hash, occurred_at, "
                "record_version, encrypted_payload FROM billing_entries WHERE owner_id = ? AND currency = ?",
                (owner, currency),
            ).fetchall()
        return self._balance_from_rows(rows, owner, currency)

    def account_currencies(self, owner_id: str) -> set[str]:
        owner = self._owner(owner_id)
        with closing(self.metadata_store.connect()) as connection:
            rows = connection.execute(
                "SELECT currency FROM billing_accounts WHERE owner_id = ?", (owner,)
            ).fetchall()
        currencies = {row[0] for row in rows}
        if len(currencies) > 1 or not all(isinstance(value, str) for value in currencies):
            raise BillingRepositoryError("billing_record_corrupt", "billing record is invalid")
        return currencies

    def _balance_in_transaction(self, connection: object, owner_id: str, currency: str) -> int:
        rows = connection.execute(
            "SELECT id, owner_id, direction, currency, amount_minor, source, operation_key_hash, occurred_at, "
            "record_version, encrypted_payload FROM billing_entries WHERE owner_id = ? AND currency = ?",
            (owner_id, currency),
        ).fetchall()
        return self._balance_from_rows(rows, owner_id, currency)

    def _balance_from_rows(self, rows: Iterable[object], owner_id: str, currency: str) -> int:
        balance = 0
        for row in rows:
            entry = self._decode(row)
            if entry.owner_id != owner_id or entry.currency != currency:
                raise BillingRepositoryError("billing_record_corrupt", "billing record is invalid")
            balance += entry.amount_minor if entry.direction is BillingDirection.CREDIT else -entry.amount_minor
        if balance < 0:
            raise BillingRepositoryError("billing_record_corrupt", "billing balance is invalid")
        return balance

    def _encode(self, entry: BillingEntry) -> bytes:
        payload = json.dumps(entry.to_storage_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self.encryption.encrypt(payload, self._aad(entry.owner_id, entry.id))

    def _decode(self, row: Iterable[object]) -> BillingEntry:
        values = tuple(row)
        if len(values) != 10:
            raise BillingRepositoryError("billing_record_corrupt", "billing record is invalid")
        entry_id, owner_id, direction, currency, amount_minor, source, operation_key_hash, occurred_at, version, envelope = values
        if version != self._RECORD_VERSION or not isinstance(envelope, bytes):
            raise BillingRepositoryError("billing_record_corrupt", "billing record is invalid")
        if not all(isinstance(value, str) for value in (entry_id, owner_id, direction, currency, source, operation_key_hash, occurred_at)):
            raise BillingRepositoryError("billing_record_corrupt", "billing record is invalid")
        try:
            plaintext = self.encryption.decrypt(envelope, self._aad(owner_id, entry_id))
            entry = BillingEntry.from_storage_dict(json.loads(plaintext.decode("utf-8")))
        except (AuthenticationError, InvalidEncryptedPayloadError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, BillingEntryValidationError) as exc:
            raise BillingRepositoryError("billing_record_corrupt", "billing record is invalid") from exc
        if (
            entry.id != entry_id
            or entry.owner_id != owner_id
            or entry.direction.value != direction
            or entry.currency != currency
            or entry.amount_minor != amount_minor
            or entry.source.value != source
            or self._operation_key_hash(entry.operation_key) != operation_key_hash
            or entry.occurred_at != occurred_at
        ):
            raise BillingRepositoryError("billing_record_corrupt", "billing record is invalid")
        return entry

    @staticmethod
    def _same_operation(first: BillingEntry, second: BillingEntry) -> bool:
        left = first.to_storage_dict()
        right = second.to_storage_dict()
        for value in (left, right):
            value.pop("id")
            # Retries generate a fresh local ID and timestamp; the idempotency
            # key binds the business operation fields, not the retry envelope.
            value.pop("occurred_at")
        return left == right

    @staticmethod
    def _operation_key_hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _find_operation(connection: object, owner_id: str, operation_key_hash: str) -> object:
        return connection.execute(
            "SELECT id, owner_id, direction, currency, amount_minor, source, operation_key_hash, occurred_at, "
            "record_version, encrypted_payload FROM billing_entries "
            "WHERE owner_id = ? AND operation_key_hash = ?",
            (owner_id, operation_key_hash),
        ).fetchone()

    @classmethod
    def _aad(cls, owner_id: str, entry_id: str) -> bytes:
        return f"{cls._AAD_PREFIX}{owner_id}/{entry_id}".encode("utf-8")

    @staticmethod
    def _owner(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise BillingRepositoryError("billing_owner_invalid", "billing owner is invalid")
        return value.strip()

    @staticmethod
    def _currency(value: object) -> str:
        if not isinstance(value, str) or len(value) != 3 or not all("A" <= char <= "Z" for char in value):
            raise BillingRepositoryError("billing_currency_invalid", "billing currency is invalid")
        return value

    @staticmethod
    def _cursor(value: object) -> tuple[str, str]:
        if not isinstance(value, tuple) or len(value) != 2:
            raise BillingRepositoryError("invalid_billing_cursor", "billing cursor is invalid")
        timestamp, entry_id = value
        if not isinstance(timestamp, str) or not isinstance(entry_id, str) or not entry_id:
            raise BillingRepositoryError("invalid_billing_cursor", "billing cursor is invalid")
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise BillingRepositoryError("invalid_billing_cursor", "billing cursor is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise BillingRepositoryError("invalid_billing_cursor", "billing cursor is invalid")
        return parsed.astimezone(UTC).isoformat(), entry_id
