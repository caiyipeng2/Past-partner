"""Verified Provider subscription event application and entitlement reads."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.domain.subscriptions import SubscriptionEvent, SubscriptionStatus, SubscriptionValidationError
from src.services.subscription_repository import SubscriptionRepository, SubscriptionRepositoryError


class SubscriptionServiceError(RuntimeError):
    """Stable subscription failure safe for API responses."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class SubscriptionService:
    def __init__(self, repository: SubscriptionRepository) -> None:
        self.repository = repository

    def apply_provider_event(
        self,
        owner_id: str,
        *,
        provider_id: str,
        provider_event_key: str,
        provider_subscription_id: str,
        plan_id: str,
        status: SubscriptionStatus | str,
        current_period_start: str,
        current_period_end: str,
        occurred_at: str,
        signature_verified: bool,
    ) -> dict[str, object]:
        if signature_verified is not True:
            raise SubscriptionServiceError(
                "subscription_event_unverified",
                "subscription provider event is not verified",
            )
        try:
            event = SubscriptionEvent(
                id=f"subscription-event-{uuid4().hex}",
                owner_id=owner_id,
                provider_id=provider_id,
                provider_event_key=provider_event_key,
                provider_subscription_id=provider_subscription_id,
                plan_id=plan_id,
                status=status,
                current_period_start=current_period_start,
                current_period_end=current_period_end,
                occurred_at=occurred_at,
            )
            return self.repository.apply(event).to_public_dict()
        except SubscriptionValidationError as exc:
            raise SubscriptionServiceError(exc.code, str(exc)) from exc
        except SubscriptionRepositoryError as exc:
            raise SubscriptionServiceError(exc.code, "subscription event could not be applied") from exc

    def current(self, owner_id: str, *, now: datetime | None = None) -> dict[str, object]:
        try:
            subscription = self.repository.get(owner_id)
        except SubscriptionRepositoryError as exc:
            raise SubscriptionServiceError(exc.code, "subscription is unavailable") from exc
        if subscription is None:
            return {"subscription": None, "entitled": False}
        observed_at = now or datetime.now(UTC)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise SubscriptionServiceError("subscription_timestamp_invalid", "subscription timestamp is invalid")
        observed_at = observed_at.astimezone(UTC)
        start = datetime.fromisoformat(subscription.current_period_start)
        end = datetime.fromisoformat(subscription.current_period_end)
        within_period = start <= observed_at < end
        entitled = subscription.status is not SubscriptionStatus.CANCELLED and within_period
        return {"subscription": subscription.to_public_dict(), "entitled": entitled}

    def export(self, owner_id: str) -> dict[str, object]:
        return self.current(owner_id)
