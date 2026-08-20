"""Validated, redacted usage records for the billing foundation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
import math
import re
from typing import Any, Mapping


class UsageRecordValidationError(ValueError):
    """Raised when usage data cannot be persisted without inventing a charge."""


class UsageOperation(str, Enum):
    CHAT = "chat"


class BillingMode(str, Enum):
    PLATFORM_BILLED = "platform_billed"
    PROVIDER_BILLED = "provider_billed"
    LOCAL_COMPUTE = "local_compute"


class UsageStatus(str, Enum):
    PRICED = "priced"
    USAGE_UNAVAILABLE = "usage_unavailable"
    PRICING_UNAVAILABLE = "pricing_unavailable"


_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class UsageRecord:
    id: str
    owner_id: str
    operation: UsageOperation
    provider_id: str
    model_id: str
    billing_mode: BillingMode
    occurred_at: str
    input_tokens: int | None
    output_tokens: int | None
    media_units: float
    currency: str | None
    provider_estimated_cost: float | None
    platform_charge: float | None
    status: UsageStatus
    provider_request_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "id", 256))
        object.__setattr__(self, "owner_id", _text(self.owner_id, "owner_id", 256))
        object.__setattr__(self, "provider_id", _text(self.provider_id, "provider_id", 128))
        object.__setattr__(self, "model_id", _text(self.model_id, "model_id", 256))
        try:
            operation = self.operation if isinstance(self.operation, UsageOperation) else UsageOperation(self.operation)
        except (TypeError, ValueError) as exc:
            raise UsageRecordValidationError("operation is invalid") from exc
        try:
            billing_mode = self.billing_mode if isinstance(self.billing_mode, BillingMode) else BillingMode(self.billing_mode)
        except (TypeError, ValueError) as exc:
            raise UsageRecordValidationError("billing_mode is invalid") from exc
        try:
            status = self.status if isinstance(self.status, UsageStatus) else UsageStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise UsageRecordValidationError("status is invalid") from exc
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "billing_mode", billing_mode)
        object.__setattr__(self, "status", status)
        normalized_time = _timestamp(self.occurred_at)
        object.__setattr__(self, "occurred_at", normalized_time)
        object.__setattr__(self, "input_tokens", _optional_count(self.input_tokens, "input_tokens"))
        object.__setattr__(self, "output_tokens", _optional_count(self.output_tokens, "output_tokens"))
        object.__setattr__(self, "media_units", _number(self.media_units, "media_units"))
        object.__setattr__(self, "currency", _optional_text(self.currency, "currency", 16))
        object.__setattr__(self, "provider_estimated_cost", _optional_money(self.provider_estimated_cost, "provider_estimated_cost"))
        object.__setattr__(self, "platform_charge", _optional_money(self.platform_charge, "platform_charge"))
        fingerprint = self.provider_request_fingerprint
        if fingerprint is not None and (not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint)):
            raise UsageRecordValidationError("provider_request_fingerprint is invalid")
        object.__setattr__(self, "provider_request_fingerprint", fingerprint)
        self._validate_status()

    @property
    def occurred_at_datetime(self) -> datetime:
        return datetime.fromisoformat(self.occurred_at)

    def _validate_status(self) -> None:
        if self.status is UsageStatus.USAGE_UNAVAILABLE:
            if self.input_tokens is not None or self.output_tokens is not None or self.media_units != 0:
                raise UsageRecordValidationError("usage_unavailable records cannot contain fabricated usage")
            if self.provider_estimated_cost is not None or self.platform_charge is not None:
                raise UsageRecordValidationError("usage_unavailable records cannot contain pricing")
            return
        if self.status is UsageStatus.PRICING_UNAVAILABLE:
            if self.input_tokens is None or self.output_tokens is None:
                raise UsageRecordValidationError("pricing_unavailable records require usage")
            if self.provider_estimated_cost is not None or self.platform_charge is not None:
                raise UsageRecordValidationError("pricing unavailable records cannot contain pricing")
            return
        if self.input_tokens is None or self.output_tokens is None:
            raise UsageRecordValidationError("priced records require usage")
        if self.currency is None:
            raise UsageRecordValidationError("priced records require currency")
        if self.provider_estimated_cost is None or self.platform_charge is None:
            raise UsageRecordValidationError("priced records require pricing")

    def to_dict(self) -> dict[str, Any]:
        """Return the client-safe view; request fingerprints remain internal."""

        return {
            "id": self.id,
            "owner_id": self.owner_id,
            "operation": self.operation.value,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "billing_mode": self.billing_mode.value,
            "occurred_at": self.occurred_at,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "media_units": self.media_units,
            "currency": self.currency,
            "provider_estimated_cost": self.provider_estimated_cost,
            "platform_charge": self.platform_charge,
            "status": self.status.value,
        }

    def to_storage_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value["provider_request_fingerprint"] = self.provider_request_fingerprint
        return value

    @classmethod
    def from_storage_dict(cls, value: object) -> "UsageRecord":
        if not isinstance(value, Mapping):
            raise UsageRecordValidationError("usage record must be an object")
        try:
            return cls(**dict(value))
        except (KeyError, TypeError) as exc:
            raise UsageRecordValidationError("usage record is incomplete") from exc


def _text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise UsageRecordValidationError(f"{field} is invalid")
    return value.strip()


def _optional_text(value: object, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field, maximum)


def _timestamp(value: object) -> str:
    if not isinstance(value, str) or len(value) > 128:
        raise UsageRecordValidationError("occurred_at is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise UsageRecordValidationError("occurred_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise UsageRecordValidationError("occurred_at is invalid")
    return parsed.astimezone(UTC).isoformat()


def _optional_count(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10**12:
        raise UsageRecordValidationError(f"{field} is invalid")
    return value


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UsageRecordValidationError(f"{field} is invalid")
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 10**12:
        raise UsageRecordValidationError(f"{field} is invalid")
    return number


def _optional_money(value: object, field: str) -> float | None:
    if value is None:
        return None
    return _number(value, field)
