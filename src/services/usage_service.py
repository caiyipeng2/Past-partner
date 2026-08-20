"""Translate normalized provider usage into an encrypted owner ledger record."""

from __future__ import annotations

from hashlib import sha256
import math
from uuid import uuid4

from src.domain.usage_records import BillingMode, UsageOperation, UsageRecord, UsageStatus
from src.providers.base import ChatRequest, ChatResponse
from src.providers.catalog import CatalogValidationError, ProviderCatalog
from src.services.usage_repository import UsageRepository, UsageRepositoryError


class UsageServiceError(RuntimeError):
    """Stable, redacted usage accounting error."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class UsageService:
    def __init__(self, repository: UsageRepository, catalog: ProviderCatalog) -> None:
        self.repository = repository
        self.catalog = catalog

    def record_chat(self, owner_id: str, request: ChatRequest, response: ChatResponse) -> UsageRecord:
        if response.provider_id != request.provider_id or response.model_id != request.model_id:
            raise UsageServiceError("usage_response_invalid", "provider usage response identity is invalid")
        billing_mode = self._billing_mode(request.provider_id)
        fingerprint = self._fingerprint(request.provider_id, response.provider_request_id)
        usage = response.usage
        if not isinstance(usage, dict):
            record = self._unavailable(owner_id, request, billing_mode, fingerprint)
            return self._append(record)
        input_tokens = _usage_count(usage, "prompt_tokens", "input_tokens")
        output_tokens = _usage_count(usage, "completion_tokens", "output_tokens")
        media_units = _usage_number(usage.get("media_units", 0))
        if input_tokens is None or output_tokens is None or media_units is None:
            record = self._unavailable(owner_id, request, billing_mode, fingerprint)
            return self._append(record)
        try:
            estimate = self.catalog.estimate_cost(
                request.provider_id,
                request.model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                media_units=media_units,
            )
        except CatalogValidationError as exc:
            if exc.code not in {"pricing_unavailable", "unknown_provider", "unknown_model"}:
                raise UsageServiceError("usage_pricing_invalid", "usage pricing could not be calculated") from exc
            record = UsageRecord(
                id=str(uuid4()),
                owner_id=owner_id,
                operation=UsageOperation.CHAT,
                provider_id=request.provider_id,
                model_id=request.model_id,
                billing_mode=billing_mode,
                occurred_at=_now(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                media_units=media_units,
                currency=self._currency(request.provider_id, request.model_id),
                provider_estimated_cost=None,
                platform_charge=None,
                status=UsageStatus.PRICING_UNAVAILABLE,
                provider_request_fingerprint=fingerprint,
            )
            return self._append(record)
        platform_charge = estimate.total_cost if billing_mode is BillingMode.PLATFORM_BILLED else 0.0
        record = UsageRecord(
            id=str(uuid4()),
            owner_id=owner_id,
            operation=UsageOperation.CHAT,
            provider_id=request.provider_id,
            model_id=request.model_id,
            billing_mode=billing_mode,
            occurred_at=_now(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            media_units=media_units,
            currency=estimate.currency,
            provider_estimated_cost=estimate.total_cost,
            platform_charge=platform_charge,
            status=UsageStatus.PRICED,
            provider_request_fingerprint=fingerprint,
        )
        return self._append(record)

    def list(self, owner_id: str, *, limit: int = 100, before: tuple[str, str] | None = None) -> list[UsageRecord]:
        try:
            return self.repository.list(owner_id, limit=limit, before=before)
        except UsageRepositoryError as exc:
            raise UsageServiceError(exc.code, "usage records are unavailable") from exc

    def _append(self, record: UsageRecord) -> UsageRecord:
        try:
            return self.repository.append(record)
        except UsageRepositoryError as exc:
            raise UsageServiceError("usage_unavailable", "usage record could not be persisted") from exc

    def _billing_mode(self, provider_id: str) -> BillingMode:
        provider = self.catalog.find_provider(provider_id)
        if provider is None:
            raise UsageServiceError("unknown_provider", "provider does not exist")
        if provider.credential_mode == "local":
            return BillingMode.LOCAL_COMPUTE
        if provider.credential_mode in {"byok", "custom"}:
            return BillingMode.PROVIDER_BILLED
        return BillingMode.PLATFORM_BILLED

    def _currency(self, provider_id: str, model_id: str) -> str | None:
        model = self.catalog.find_model(provider_id, model_id)
        return model.pricing.currency if model is not None else None

    @staticmethod
    def _fingerprint(provider_id: str, provider_request_id: str | None) -> str | None:
        if provider_request_id is None:
            return None
        if not isinstance(provider_request_id, str) or not provider_request_id.strip():
            raise UsageServiceError("usage_response_invalid", "provider usage response identity is invalid")
        return sha256(f"{provider_id}\x00{provider_request_id}".encode("utf-8")).hexdigest()

    @staticmethod
    def _unavailable(
        owner_id: str,
        request: ChatRequest,
        billing_mode: BillingMode,
        fingerprint: str | None,
    ) -> UsageRecord:
        return UsageRecord(
            id=str(uuid4()),
            owner_id=owner_id,
            operation=UsageOperation.CHAT,
            provider_id=request.provider_id,
            model_id=request.model_id,
            billing_mode=billing_mode,
            occurred_at=_now(),
            input_tokens=None,
            output_tokens=None,
            media_units=0,
            currency=None,
            provider_estimated_cost=None,
            platform_charge=None,
            status=UsageStatus.USAGE_UNAVAILABLE,
            provider_request_fingerprint=fingerprint,
        )


def _usage_count(usage: dict[str, int], primary: str, alternate: str) -> int | None:
    value = usage.get(primary, usage.get(alternate))
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10**12:
        return None
    return value


def _usage_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 10**12:
        return None
    return number


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
