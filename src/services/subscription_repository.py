"""Encrypted subscription snapshots and provider-event idempotency records."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import closing
from datetime import datetime
from hashlib import sha256
import json

from src.domain.subscriptions import Subscription, SubscriptionEvent, SubscriptionValidationError
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import MetadataIntegrityError, MetadataStore, require_metadata_store


class SubscriptionRepositoryError(RuntimeError):
    """Stable subscription failure without provider or database details."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class SubscriptionRepository:
    _RECORD_VERSION = 1
    _SUBSCRIPTION_AAD = "past-partner/subscription/v1/"
    _EVENT_AAD = "past-partner/subscription-event/v1/"

    def __init__(self, metadata_store: MetadataStore, encryption: AuthenticatedEncryptionService) -> None:
        self.metadata_store = require_metadata_store(metadata_store)
        self.encryption = encryption
        self.metadata_store.migrate()

    def apply(self, event: SubscriptionEvent) -> Subscription:
        if not isinstance(event, SubscriptionEvent):
            raise TypeError("event must be a SubscriptionEvent")
        event_hash = self._event_hash(event.provider_event_key)
        subscription_hash = self._subscription_hash(event.provider_id, event.provider_subscription_id)
        event_envelope = self._encode_event(event)
        try:
            with self.metadata_store.transaction(immediate=self.metadata_store.backend_name == "sqlite") as connection:
                self._lock_owner(connection, event.owner_id)
                existing_event_row = self._find_event(connection, event.provider_id, event_hash)
                if existing_event_row is not None:
                    existing_event = self._decode_event(existing_event_row)
                    if self._same_event(existing_event, event):
                        current = self._get_in_transaction(connection, event.owner_id)
                        return current or existing_event.to_subscription()
                    raise SubscriptionRepositoryError("subscription_event_conflict", "subscription event conflicts")

                current = self._get_in_transaction(connection, event.owner_id)
                if current is not None and (
                    current.provider_id != event.provider_id
                    or current.provider_subscription_id != event.provider_subscription_id
                ) and not self._identity_switch_allowed(current, event):
                    raise SubscriptionRepositoryError("subscription_identity_conflict", "subscription identity conflicts")
                self._ensure_subscription_binding(connection, event, subscription_hash)
                candidate = event.to_subscription()
                if current is not None and event.occurred_at == current.updated_at and candidate.to_dict() != current.to_dict():
                    raise SubscriptionRepositoryError(
                        "subscription_timestamp_conflict",
                        "subscription event timestamp conflicts",
                    )
                connection.execute(
                    """
                    INSERT INTO subscription_events
                        (id, owner_id, provider_id, provider_event_key_hash, provider_subscription_hash,
                         occurred_at, record_version, encrypted_payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.id,
                        event.owner_id,
                        event.provider_id,
                        event_hash,
                        subscription_hash,
                        event.occurred_at,
                        self._RECORD_VERSION,
                        event_envelope,
                    ),
                )
                if current is None or event.occurred_at > current.updated_at:
                    subscription_envelope = self._encode_subscription(candidate)
                    connection.execute(
                        """
                        INSERT INTO subscriptions (owner_id, record_version, encrypted_payload, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(owner_id) DO UPDATE SET
                            record_version = excluded.record_version,
                            encrypted_payload = excluded.encrypted_payload,
                            updated_at = excluded.updated_at
                        """,
                        (event.owner_id, self._RECORD_VERSION, subscription_envelope, candidate.updated_at),
                    )
                    return candidate
                return current
        except SubscriptionRepositoryError:
            raise
        except MetadataIntegrityError as exc:
            raise SubscriptionRepositoryError("subscription_event_conflict", "subscription event conflicts") from exc

    def get(self, owner_id: str) -> Subscription | None:
        owner = self._owner(owner_id)
        with closing(self.metadata_store.connect()) as connection:
            row = connection.execute(
                "SELECT owner_id, record_version, encrypted_payload, updated_at FROM subscriptions WHERE owner_id = ?",
                (owner,),
            ).fetchone()
        return self._decode_subscription(row) if row is not None else None

    def list_events(self, owner_id: str) -> list[SubscriptionEvent]:
        owner = self._owner(owner_id)
        with closing(self.metadata_store.connect()) as connection:
            rows = connection.execute(
                "SELECT id, owner_id, provider_id, provider_event_key_hash, provider_subscription_hash, occurred_at, record_version, encrypted_payload "
                "FROM subscription_events WHERE owner_id = ? ORDER BY occurred_at ASC, id ASC",
                (owner,),
            ).fetchall()
        return [self._decode_event(row) for row in rows]

    def _get_in_transaction(self, connection: object, owner_id: str) -> Subscription | None:
        row = connection.execute(
            "SELECT owner_id, record_version, encrypted_payload, updated_at FROM subscriptions WHERE owner_id = ?",
            (owner_id,),
        ).fetchone()
        return self._decode_subscription(row) if row is not None else None

    @staticmethod
    def _lock_owner(connection: object, owner_id: str) -> None:
        result = connection.execute("UPDATE local_users SET id = id WHERE id = ?", (owner_id,))
        if getattr(result, "rowcount", 0) != 1:
            raise SubscriptionRepositoryError("subscription_owner_invalid", "subscription owner is invalid")

    @staticmethod
    def _find_event(connection: object, provider_id: str, event_hash: str) -> object:
        return connection.execute(
            "SELECT id, owner_id, provider_id, provider_event_key_hash, provider_subscription_hash, occurred_at, record_version, encrypted_payload "
            "FROM subscription_events WHERE provider_id = ? AND provider_event_key_hash = ?",
            (provider_id, event_hash),
        ).fetchone()

    @staticmethod
    def _ensure_subscription_binding(connection: object, event: SubscriptionEvent, subscription_hash: str) -> None:
        connection.execute(
            "INSERT INTO subscription_bindings "
            "(provider_id, provider_subscription_hash, owner_id, record_version) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(provider_id, provider_subscription_hash) DO NOTHING",
            (event.provider_id, subscription_hash, event.owner_id, SubscriptionRepository._RECORD_VERSION),
        )
        row = connection.execute(
            "SELECT owner_id FROM subscription_bindings WHERE provider_id = ? AND provider_subscription_hash = ?",
            (event.provider_id, subscription_hash),
        ).fetchone()
        if row is None or row[0] != event.owner_id:
            raise SubscriptionRepositoryError("subscription_identity_conflict", "subscription identity conflicts")

    @staticmethod
    def _identity_switch_allowed(current: Subscription, event: SubscriptionEvent) -> bool:
        if event.occurred_at <= current.updated_at:
            return False
        return (
            current.status.value == "cancelled"
            or datetime.fromisoformat(current.current_period_end) <= datetime.fromisoformat(event.occurred_at)
        )

    def _encode_event(self, event: SubscriptionEvent) -> bytes:
        payload = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self.encryption.encrypt(payload, self._event_aad(event.owner_id, event.id))

    def _encode_subscription(self, subscription: Subscription) -> bytes:
        payload = json.dumps(subscription.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self.encryption.encrypt(payload, self._subscription_aad(subscription.owner_id))

    def _decode_event(self, row: Iterable[object]) -> SubscriptionEvent:
        values = tuple(row)
        if len(values) != 8:
            raise SubscriptionRepositoryError("subscription_record_corrupt", "subscription record is invalid")
        event_id, owner_id, provider_id, event_hash, subscription_hash, occurred_at, version, envelope = values
        if version != self._RECORD_VERSION or not isinstance(envelope, bytes):
            raise SubscriptionRepositoryError("subscription_record_corrupt", "subscription record is invalid")
        if not all(isinstance(value, str) for value in (event_id, owner_id, provider_id, event_hash, subscription_hash, occurred_at)):
            raise SubscriptionRepositoryError("subscription_record_corrupt", "subscription record is invalid")
        try:
            plaintext = self.encryption.decrypt(envelope, self._event_aad(owner_id, event_id))
            event = SubscriptionEvent.from_storage_dict(json.loads(plaintext.decode("utf-8")))
        except (AuthenticationError, InvalidEncryptedPayloadError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, SubscriptionValidationError) as exc:
            raise SubscriptionRepositoryError("subscription_record_corrupt", "subscription record is invalid") from exc
        if (
            event.id != event_id
            or event.owner_id != owner_id
            or event.provider_id != provider_id
            or self._event_hash(event.provider_event_key) != event_hash
            or self._subscription_hash(event.provider_id, event.provider_subscription_id) != subscription_hash
            or event.occurred_at != occurred_at
        ):
            raise SubscriptionRepositoryError("subscription_record_corrupt", "subscription record is invalid")
        return event

    def _decode_subscription(self, row: Iterable[object]) -> Subscription:
        values = tuple(row)
        if len(values) != 4:
            raise SubscriptionRepositoryError("subscription_record_corrupt", "subscription record is invalid")
        owner_id, version, envelope, updated_at = values
        if version != self._RECORD_VERSION or not isinstance(owner_id, str) or not isinstance(envelope, bytes) or not isinstance(updated_at, str):
            raise SubscriptionRepositoryError("subscription_record_corrupt", "subscription record is invalid")
        try:
            plaintext = self.encryption.decrypt(envelope, self._subscription_aad(owner_id))
            subscription = Subscription(**json.loads(plaintext.decode("utf-8")))
        except (AuthenticationError, InvalidEncryptedPayloadError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, SubscriptionValidationError) as exc:
            raise SubscriptionRepositoryError("subscription_record_corrupt", "subscription record is invalid") from exc
        if subscription.owner_id != owner_id or subscription.updated_at != updated_at:
            raise SubscriptionRepositoryError("subscription_record_corrupt", "subscription record is invalid")
        return subscription

    @staticmethod
    def _same_event(first: SubscriptionEvent, second: SubscriptionEvent) -> bool:
        left = first.to_dict()
        right = second.to_dict()
        left.pop("id")
        right.pop("id")
        return left == right

    @staticmethod
    def _event_hash(value: str) -> str:
        return sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _subscription_hash(provider_id: str, subscription_id: str) -> str:
        return sha256(f"{provider_id}:{subscription_id}".encode("utf-8")).hexdigest()

    @classmethod
    def _event_aad(cls, owner_id: str, event_id: str) -> bytes:
        return f"{cls._EVENT_AAD}{owner_id}/{event_id}".encode("utf-8")

    @classmethod
    def _subscription_aad(cls, owner_id: str) -> bytes:
        return f"{cls._SUBSCRIPTION_AAD}{owner_id}".encode("utf-8")

    @staticmethod
    def _owner(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise SubscriptionRepositoryError("subscription_owner_invalid", "subscription owner is invalid")
        return value.strip()
