"""Validated subscription events and current entitlement snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import re
from typing import Any, Mapping


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_PROVIDER_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class SubscriptionValidationError(ValueError):
    """Stable validation failure without echoing provider payloads."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class SubscriptionStatus(str, Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SubscriptionEvent:
    id: str
    owner_id: str
    provider_id: str
    provider_event_key: str
    provider_subscription_id: str
    plan_id: str
    status: SubscriptionStatus
    current_period_start: str
    current_period_end: str
    occurred_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier(self.id, "subscription_event_id_invalid"))
        object.__setattr__(self, "owner_id", _identifier(self.owner_id, "subscription_owner_invalid"))
        object.__setattr__(self, "provider_id", _provider_identifier(self.provider_id))
        object.__setattr__(self, "provider_event_key", _identifier(self.provider_event_key, "subscription_event_key_invalid"))
        object.__setattr__(self, "provider_subscription_id", _identifier(self.provider_subscription_id, "subscription_id_invalid"))
        object.__setattr__(self, "plan_id", _identifier(self.plan_id, "subscription_plan_invalid"))
        try:
            status = self.status if isinstance(self.status, SubscriptionStatus) else SubscriptionStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise SubscriptionValidationError("subscription_status_invalid", "subscription status is invalid") from exc
        object.__setattr__(self, "status", status)
        period_start = _timestamp(self.current_period_start, "subscription_period_invalid")
        period_end = _timestamp(self.current_period_end, "subscription_period_invalid")
        if datetime.fromisoformat(period_end) <= datetime.fromisoformat(period_start):
            raise SubscriptionValidationError("subscription_period_invalid", "subscription period is invalid")
        object.__setattr__(self, "current_period_start", period_start)
        object.__setattr__(self, "current_period_end", period_end)
        object.__setattr__(self, "occurred_at", _timestamp(self.occurred_at, "subscription_timestamp_invalid"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "provider_id": self.provider_id,
            "provider_event_key": self.provider_event_key,
            "provider_subscription_id": self.provider_subscription_id,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "current_period_start": self.current_period_start,
            "current_period_end": self.current_period_end,
            "occurred_at": self.occurred_at,
        }

    def to_public_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value.pop("provider_event_key")
        return value

    @classmethod
    def from_storage_dict(cls, value: object) -> "SubscriptionEvent":
        if not isinstance(value, Mapping):
            raise SubscriptionValidationError("subscription_record_corrupt", "subscription record is invalid")
        try:
            return cls(**dict(value))
        except (KeyError, TypeError) as exc:
            raise SubscriptionValidationError("subscription_record_corrupt", "subscription record is invalid") from exc

    def to_subscription(self) -> "Subscription":
        return Subscription(
            id=self.provider_subscription_id,
            owner_id=self.owner_id,
            provider_id=self.provider_id,
            provider_subscription_id=self.provider_subscription_id,
            plan_id=self.plan_id,
            status=self.status,
            current_period_start=self.current_period_start,
            current_period_end=self.current_period_end,
            updated_at=self.occurred_at,
        )


@dataclass(frozen=True, slots=True)
class Subscription:
    id: str
    owner_id: str
    provider_id: str
    provider_subscription_id: str
    plan_id: str
    status: SubscriptionStatus
    current_period_start: str
    current_period_end: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier(self.id, "subscription_id_invalid"))
        object.__setattr__(self, "owner_id", _identifier(self.owner_id, "subscription_owner_invalid"))
        object.__setattr__(self, "provider_id", _provider_identifier(self.provider_id))
        object.__setattr__(self, "provider_subscription_id", _identifier(self.provider_subscription_id, "subscription_id_invalid"))
        object.__setattr__(self, "plan_id", _identifier(self.plan_id, "subscription_plan_invalid"))
        try:
            status = self.status if isinstance(self.status, SubscriptionStatus) else SubscriptionStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise SubscriptionValidationError("subscription_status_invalid", "subscription status is invalid") from exc
        object.__setattr__(self, "status", status)
        start = _timestamp(self.current_period_start, "subscription_period_invalid")
        end = _timestamp(self.current_period_end, "subscription_period_invalid")
        if datetime.fromisoformat(end) <= datetime.fromisoformat(start):
            raise SubscriptionValidationError("subscription_period_invalid", "subscription period is invalid")
        object.__setattr__(self, "current_period_start", start)
        object.__setattr__(self, "current_period_end", end)
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at, "subscription_timestamp_invalid"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "provider_id": self.provider_id,
            "provider_subscription_id": self.provider_subscription_id,
            "plan_id": self.plan_id,
            "status": self.status.value,
            "current_period_start": self.current_period_start,
            "current_period_end": self.current_period_end,
            "updated_at": self.updated_at,
        }

    def to_public_dict(self) -> dict[str, Any]:
        return self.to_dict()


def _identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise SubscriptionValidationError(code, "subscription identifier is invalid")
    return value


def _provider_identifier(value: object) -> str:
    if not isinstance(value, str) or _PROVIDER_IDENTIFIER.fullmatch(value) is None:
        raise SubscriptionValidationError("subscription_provider_invalid", "subscription identifier is invalid")
    return value


def _timestamp(value: object, code: str) -> str:
    if not isinstance(value, str) or len(value) > 128:
        raise SubscriptionValidationError(code, "subscription timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SubscriptionValidationError(code, "subscription timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SubscriptionValidationError(code, "subscription timestamp is invalid")
    return parsed.astimezone(UTC).isoformat()
