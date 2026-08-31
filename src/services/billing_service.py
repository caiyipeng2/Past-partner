"""Owner-scoped billing operations for future verified payment adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.domain.billing import BillingDirection, BillingEntry, BillingEntryValidationError, BillingSource
from src.services.billing_repository import BillingRepository, BillingRepositoryError


class BillingServiceError(RuntimeError):
    """Stable billing failure safe for API responses."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class BillingService:
    def __init__(self, repository: BillingRepository) -> None:
        self.repository = repository

    def credit(
        self,
        owner_id: str,
        *,
        amount_minor: int,
        currency: str,
        operation_key: str,
        source: BillingSource | str = BillingSource.PAYMENT,
    ) -> BillingEntry:
        return self._post(
            owner_id,
            amount_minor=amount_minor,
            currency=currency,
            operation_key=operation_key,
            direction=BillingDirection.CREDIT,
            source=source,
        )

    def debit(
        self,
        owner_id: str,
        *,
        amount_minor: int,
        currency: str,
        operation_key: str,
        source: BillingSource | str = BillingSource.USAGE,
    ) -> BillingEntry:
        return self._post(
            owner_id,
            amount_minor=amount_minor,
            currency=currency,
            operation_key=operation_key,
            direction=BillingDirection.DEBIT,
            source=source,
        )

    def balance(self, owner_id: str, currency: str) -> dict[str, int | str]:
        if not isinstance(currency, str) or len(currency) != 3 or not all("A" <= char <= "Z" for char in currency):
            raise BillingServiceError("billing_currency_invalid", "billing currency is invalid")
        try:
            currencies = self.repository.account_currencies(owner_id)
            if currencies and currency not in currencies:
                raise BillingServiceError("billing_currency_mismatch", "billing currency does not match the account")
            return {"currency": currency, "balance_minor": self.repository.balance(owner_id, currency)}
        except BillingServiceError:
            raise
        except BillingRepositoryError as exc:
            raise BillingServiceError(exc.code, "billing balance is unavailable") from exc

    def list_entries(
        self,
        owner_id: str,
        *,
        limit: int = 100,
        before: tuple[str, str] | None = None,
    ) -> list[dict[str, object]]:
        try:
            return [entry.to_public_dict() for entry in self.repository.list(owner_id, limit=limit, before=before)]
        except BillingRepositoryError as exc:
            raise BillingServiceError(exc.code, "billing entries are unavailable") from exc

    def export(self, owner_id: str) -> dict[str, list[dict[str, object]]]:
        """Return owner-scoped financial metadata for a data export."""
        try:
            currencies = sorted(self.repository.account_currencies(owner_id))
            entries: list[dict[str, object]] = []
            before: tuple[str, str] | None = None
            while True:
                page = self.repository.list(owner_id, limit=self.repository._MAX_LIMIT, before=before)
                entries.extend(entry.to_public_dict() for entry in page)
                if len(page) < self.repository._MAX_LIMIT:
                    break
                before = (page[-1].occurred_at, page[-1].id)
            return {
                "balances": [self.balance(owner_id, currency) for currency in currencies],
                "entries": entries,
            }
        except BillingServiceError:
            raise
        except BillingRepositoryError as exc:
            raise BillingServiceError(exc.code, "billing export is unavailable") from exc

    def _post(self, owner_id: str, *, amount_minor: int, currency: str, operation_key: str, direction: BillingDirection, source: BillingSource | str) -> BillingEntry:
        try:
            entry = BillingEntry(
                id=f"billing-{uuid4().hex}",
                owner_id=owner_id,
                direction=direction,
                currency=currency,
                amount_minor=amount_minor,
                source=source,
                operation_key=operation_key,
                occurred_at=datetime.now(UTC).isoformat(),
            )
            return self.repository.append(entry, enforce_balance=direction is BillingDirection.DEBIT)
        except BillingEntryValidationError as exc:
            raise BillingServiceError(exc.code, str(exc)) from exc
        except BillingRepositoryError as exc:
            raise BillingServiceError(exc.code, "billing entry could not be recorded") from exc
