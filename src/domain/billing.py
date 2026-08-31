"""Validated, append-only billing entries for the commercial foundation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import re
from typing import Any, Mapping


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_MAX_AMOUNT_MINOR = 10**12


class BillingEntryValidationError(ValueError):
    """Stable validation failure without echoing financial input."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class BillingDirection(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class BillingSource(str, Enum):
    PAYMENT = "payment"
    REFUND = "refund"
    USAGE = "usage"
    SUBSCRIPTION = "subscription"


@dataclass(frozen=True, slots=True)
class BillingEntry:
    id: str
    owner_id: str
    direction: BillingDirection
    currency: str
    amount_minor: int
    source: BillingSource
    operation_key: str
    occurred_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier(self.id, "billing_id_invalid"))
        object.__setattr__(self, "owner_id", _identifier(self.owner_id, "billing_owner_invalid"))
        object.__setattr__(self, "operation_key", _identifier(self.operation_key, "billing_operation_key_invalid"))
        if not isinstance(self.currency, str) or _CURRENCY.fullmatch(self.currency) is None:
            raise BillingEntryValidationError("billing_currency_invalid", "billing currency is invalid")
        object.__setattr__(self, "currency", self.currency)
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int) or not 1 <= self.amount_minor <= _MAX_AMOUNT_MINOR:
            raise BillingEntryValidationError("billing_amount_invalid", "billing amount is invalid")
        try:
            direction = self.direction if isinstance(self.direction, BillingDirection) else BillingDirection(self.direction)
            source = self.source if isinstance(self.source, BillingSource) else BillingSource(self.source)
        except (TypeError, ValueError) as exc:
            raise BillingEntryValidationError("billing_source_invalid", "billing source is invalid") from exc
        if direction is BillingDirection.CREDIT and source not in {BillingSource.PAYMENT, BillingSource.REFUND}:
            raise BillingEntryValidationError("billing_source_invalid", "billing source is invalid")
        if direction is BillingDirection.DEBIT and source not in {BillingSource.USAGE, BillingSource.SUBSCRIPTION}:
            raise BillingEntryValidationError("billing_source_invalid", "billing source is invalid")
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "occurred_at", _timestamp(self.occurred_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "direction": self.direction.value,
            "currency": self.currency,
            "amount_minor": self.amount_minor,
            "source": self.source.value,
            "operation_key": self.operation_key,
            "occurred_at": self.occurred_at,
        }

    def to_public_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value.pop("operation_key")
        return value

    def to_storage_dict(self) -> dict[str, Any]:
        return self.to_dict()

    @classmethod
    def from_storage_dict(cls, value: object) -> "BillingEntry":
        if not isinstance(value, Mapping):
            raise BillingEntryValidationError("billing_record_corrupt", "billing record is invalid")
        try:
            return cls(**dict(value))
        except (KeyError, TypeError) as exc:
            raise BillingEntryValidationError("billing_record_corrupt", "billing record is invalid") from exc


def _identifier(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise BillingEntryValidationError(code, "billing identifier is invalid")
    return value


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or len(value) > 128:
        raise BillingEntryValidationError("billing_timestamp_invalid", "billing timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BillingEntryValidationError("billing_timestamp_invalid", "billing timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BillingEntryValidationError("billing_timestamp_invalid", "billing timestamp is invalid")
    return parsed.astimezone(UTC).isoformat()
