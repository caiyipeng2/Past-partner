"""One validation and error boundary for all model providers."""

from __future__ import annotations

from collections.abc import Mapping

from src.providers.base import AdapterError, ChatRequest, ChatResponse, ProviderAdapter
from src.providers.catalog import ProviderCatalog


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProviderGateway:
    def __init__(
        self,
        catalog: ProviderCatalog,
        mode: str,
        adapters: Mapping[str, ProviderAdapter] | None = None,
    ):
        self.catalog = catalog
        self.mode = mode
        self.adapters = dict(adapters or {})
        if "test" in self.adapters and mode != "test":
            raise ProviderError("test_provider_disabled", "the deterministic provider is test-only")

    def chat(self, request: ChatRequest) -> ChatResponse:
        if request.provider_id == "test":
            if self.mode != "test":
                raise ProviderError("test_provider_disabled", "the deterministic provider is test-only")
            adapter = self.adapters.get("test")
            if adapter is None or not adapter.supports_model(request.model_id):
                raise ProviderError("unknown_model", "test model is not available")
            return adapter.chat(request)

        provider = self.catalog.find_provider(request.provider_id)
        if provider is None:
            raise ProviderError("unknown_provider", "provider does not exist")

        catalog_model = self.catalog.find_model(request.provider_id, request.model_id)
        adapter = self.adapters.get(request.provider_id)
        if provider.model_discovery == "catalog" and catalog_model is None:
            raise ProviderError("unknown_model", "model is not in the provider catalog")
        if provider.model_discovery != "catalog" and adapter is not None and not adapter.supports_model(request.model_id):
            raise ProviderError("unknown_model", "model is not configured for this provider")
        if adapter is None:
            raise ProviderError("provider_not_configured", "provider credentials or endpoint are not configured")
        if not adapter.supports_model(request.model_id):
            raise ProviderError("unknown_model", "adapter does not allow this model")

        try:
            return adapter.chat(request)
        except AdapterError as exc:
            raise ProviderError(exc.code, str(exc)) from exc
