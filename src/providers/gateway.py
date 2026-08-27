"""One validation and error boundary for all model providers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from src.providers.base import (
    AdapterError,
    ChatRequest,
    ChatResponse,
    FineTuningProviderAdapter,
    FineTuningRequest,
    FineTuningStatus,
    FineTuningSubmission,
    MediaAnalysisProviderAdapter,
    MediaAnalysisRequest,
    MediaAnalysisResult,
    ProviderAdapter,
)
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
        # The adapter identity matters as much as its mapping key: a test adapter
        # must not become reachable by being registered under a real provider ID.
        if mode != "test" and (
            "test" in self.adapters
            or any(getattr(adapter, "provider_id", None) == "test" for adapter in self.adapters.values())
        ):
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

    def analyze_media(self, request: MediaAnalysisRequest) -> MediaAnalysisResult:
        """Analyze one controlled media payload through an advertised adapter.

        Capability metadata is checked before adapter lookup or invocation so a
        caller cannot turn an unavailable model into an implicit chat request.
        The adapter is a separate optional protocol because media providers may
        not implement text chat with the same endpoint or credentials.
        """

        category = _media_category(request.media_type)
        adapter = self._media_analysis_adapter(request.provider_id, request.model_id, category)
        try:
            result = adapter.analyze_media(request)
        except AdapterError as exc:
            raise ProviderError(exc.code, str(exc)) from exc
        except (AttributeError, TypeError) as exc:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider media adapter did not satisfy the analysis contract",
            ) from exc
        if not isinstance(result, MediaAnalysisResult):
            raise ProviderError(
                "invalid_provider_adapter",
                "provider media adapter returned an invalid analysis result",
            )
        if (
            result.provider_id != request.provider_id
            or result.model_id != request.model_id
            or result.media_type != request.media_type
        ):
            raise ProviderError(
                "invalid_provider_adapter",
                "provider media adapter returned mismatched result metadata",
            )
        return result

    def submit_fine_tuning(self, request: FineTuningRequest) -> FineTuningSubmission:
        adapter = self._fine_tuning_adapter(request.provider_id, request.model_id)
        try:
            return adapter.submit_fine_tuning(request)
        except AdapterError as exc:
            raise ProviderError(exc.code, str(exc)) from exc
        except (AttributeError, TypeError) as exc:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider fine-tuning adapter did not satisfy the submission contract",
            ) from exc

    def validate_fine_tuning(self, provider_id: str, model_id: str) -> None:
        """Check the catalog and adapter handoff boundary without transferring data.

        Fine-tuning datasets may contain sensitive persona-authored text.  Callers
        use this explicit preflight before materializing a temporary JSONL file so
        an unavailable capability cannot trigger needless decryption or disk use.
        """

        self._fine_tuning_adapter(provider_id, model_id)

    def get_fine_tuning_job(
        self,
        provider_id: str,
        model_id: str,
        provider_job_id: str,
    ) -> FineTuningStatus:
        adapter = self._fine_tuning_adapter(provider_id, model_id)
        try:
            return adapter.get_fine_tuning_job(provider_job_id)
        except AdapterError as exc:
            raise ProviderError(exc.code, str(exc)) from exc
        except (AttributeError, TypeError) as exc:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider fine-tuning adapter did not satisfy the status contract",
            ) from exc

    def recover_fine_tuning_submission(
        self,
        provider_id: str,
        model_id: str,
        client_job_id: str,
    ) -> FineTuningSubmission | None:
        """Reconcile an uncertain post-handoff write using the client job ID."""

        adapter = self._fine_tuning_adapter(provider_id, model_id)
        try:
            return adapter.recover_fine_tuning_submission(client_job_id)
        except AdapterError as exc:
            raise ProviderError(exc.code, str(exc)) from exc
        except (AttributeError, TypeError) as exc:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider fine-tuning adapter did not satisfy the recovery contract",
            ) from exc

    def cancel_fine_tuning_job(
        self,
        provider_id: str,
        model_id: str,
        provider_job_id: str,
    ) -> FineTuningStatus:
        adapter = self._fine_tuning_adapter(provider_id, model_id)
        try:
            return adapter.cancel_fine_tuning_job(provider_job_id)
        except AdapterError as exc:
            raise ProviderError(exc.code, str(exc)) from exc
        except (AttributeError, TypeError) as exc:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider fine-tuning adapter did not satisfy the cancellation contract",
            ) from exc

    def _fine_tuning_adapter(
        self,
        provider_id: str,
        model_id: str,
    ) -> FineTuningProviderAdapter:
        if provider_id == "test":
            if self.mode != "test":
                raise ProviderError("test_provider_disabled", "the deterministic provider is test-only")
            # Test mode proves the same catalog contract as a real provider. The
            # deterministic adapter alone is never enough to advertise a model as
            # trainable, otherwise tests could bypass missing capability metadata.
            provider = self.catalog.find_provider(provider_id)
            if provider is None:
                raise ProviderError("unknown_provider", "provider does not exist")
            model = self.catalog.find_model(provider_id, model_id)
            if model is None:
                raise ProviderError("unknown_model", "model is not in the provider catalog")
            if "fine_tuning" not in provider.capabilities or "fine_tuning" not in model.capabilities:
                raise ProviderError("capability_not_supported", "model does not support fine-tuning")
            adapter = self._validated_fine_tuning_adapter(
                self.adapters.get("test"),
                expected_provider_id="test",
            )
            if adapter is None:
                raise ProviderError("unknown_model", "test model is not available for fine-tuning")
            if not self._supports_fine_tuning(adapter, model_id):
                raise ProviderError("unknown_model", "test model is not available for fine-tuning")
            return adapter

        provider = self.catalog.find_provider(provider_id)
        if provider is None:
            raise ProviderError("unknown_provider", "provider does not exist")
        model = self.catalog.find_model(provider_id, model_id)
        if model is None:
            raise ProviderError("unknown_model", "model is not in the provider catalog")
        if "fine_tuning" not in provider.capabilities or "fine_tuning" not in model.capabilities:
            raise ProviderError("capability_not_supported", "model does not support fine-tuning")

        adapter = self.adapters.get(provider_id)
        fine_tuning_adapter = self._validated_fine_tuning_adapter(
            adapter,
            expected_provider_id=provider_id,
        )
        if fine_tuning_adapter is None:
            raise ProviderError("provider_not_configured", "provider fine-tuning is not configured")
        if not self._supports_fine_tuning(fine_tuning_adapter, model_id):
            raise ProviderError("capability_not_supported", "adapter does not support fine-tuning for this model")
        return fine_tuning_adapter

    def _media_analysis_adapter(
        self,
        provider_id: str,
        model_id: str,
        media_category: str,
    ) -> MediaAnalysisProviderAdapter:
        required_capability = _media_capability(media_category)
        if provider_id == "test":
            if self.mode != "test":
                raise ProviderError("test_provider_disabled", "the deterministic provider is test-only")
            provider = self.catalog.find_provider(provider_id)
            if provider is None:
                raise ProviderError("unknown_provider", "provider does not exist")
            model = self.catalog.find_model(provider_id, model_id)
            if model is None:
                raise ProviderError("unknown_model", "model is not in the provider catalog")
            if required_capability not in provider.capabilities or required_capability not in model.capabilities:
                raise ProviderError("capability_not_supported", "model does not support media analysis")
            adapter = self._validated_media_analysis_adapter(
                self.adapters.get(provider_id),
                expected_provider_id=provider_id,
            )
            if adapter is None:
                raise ProviderError("provider_not_configured", "provider media analysis is not configured")
            if not self._supports_media(adapter, model_id, media_category):
                raise ProviderError("capability_not_supported", "adapter does not support this media category")
            return adapter

        provider = self.catalog.find_provider(provider_id)
        if provider is None:
            raise ProviderError("unknown_provider", "provider does not exist")
        model = self.catalog.find_model(provider_id, model_id)
        if model is None:
            raise ProviderError("unknown_model", "model is not in the provider catalog")
        if required_capability not in provider.capabilities or required_capability not in model.capabilities:
            raise ProviderError("capability_not_supported", "model does not support media analysis")
        adapter = self._validated_media_analysis_adapter(
            self.adapters.get(provider_id),
            expected_provider_id=provider_id,
        )
        if adapter is None:
            raise ProviderError("provider_not_configured", "provider media analysis is not configured")
        if not self._supports_media(adapter, model_id, media_category):
            raise ProviderError("capability_not_supported", "adapter does not support this media category")
        return adapter

    def _validated_media_analysis_adapter(
        self,
        adapter: object,
        *,
        expected_provider_id: str,
    ) -> MediaAnalysisProviderAdapter | None:
        if adapter is None:
            return None
        try:
            adapter_provider_id = getattr(adapter, "provider_id")
            supports_media = getattr(adapter, "supports_media", None)
            analyze_media = getattr(adapter, "analyze_media", None)
        except (AttributeError, TypeError) as exc:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider media adapter identity or methods are inaccessible",
            ) from exc
        if adapter_provider_id == "test" and self.mode != "test":
            raise ProviderError("test_provider_disabled", "the deterministic provider is test-only")
        if adapter_provider_id != expected_provider_id:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider media adapter identity does not match the requested provider",
            )
        if not callable(supports_media) or not callable(analyze_media):
            raise ProviderError("invalid_provider_adapter", "provider media adapter is malformed")
        return cast(MediaAnalysisProviderAdapter, adapter)

    @staticmethod
    def _supports_media(
        adapter: MediaAnalysisProviderAdapter,
        model_id: str,
        media_category: str,
    ) -> bool:
        try:
            supported = adapter.supports_media(model_id, media_category)
        except AdapterError as exc:
            raise ProviderError(exc.code, str(exc)) from exc
        except (AttributeError, TypeError) as exc:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider media adapter did not satisfy the capability contract",
            ) from exc
        if not isinstance(supported, bool):
            raise ProviderError(
                "invalid_provider_adapter",
                "provider media capability result must be a boolean",
            )
        return supported

    def _validated_fine_tuning_adapter(
        self,
        adapter: object,
        *,
        expected_provider_id: str,
    ) -> FineTuningProviderAdapter | None:
        required_methods = (
            "supports_fine_tuning",
            "submit_fine_tuning",
            "recover_fine_tuning_submission",
            "get_fine_tuning_job",
            "cancel_fine_tuning_job",
        )
        if adapter is None:
            return None
        try:
            adapter_provider_id = getattr(adapter, "provider_id")
            methods = tuple(getattr(adapter, method_name, None) for method_name in required_methods)
        except (AttributeError, TypeError) as exc:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider fine-tuning adapter identity or methods are inaccessible",
            ) from exc
        # This is rechecked at every call, rather than only in __init__, because the
        # adapter registry is a mutable integration boundary in the local runtime.
        # A test adapter or a provider-specific credential set must never be routed
        # merely because somebody later reused a different mapping key.
        if adapter_provider_id == "test" and self.mode != "test":
            raise ProviderError("test_provider_disabled", "the deterministic provider is test-only")
        if adapter_provider_id != expected_provider_id:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider fine-tuning adapter identity does not match the requested provider",
            )
        if not any(method is not None for method in methods):
            return None
        # Runtime Protocol checks establish attribute presence, but do not prove
        # those attributes are callable. Reject malformed adapters before any
        # operation can expose a raw TypeError through the gateway boundary.
        if not all(callable(method) for method in methods):
            raise ProviderError("invalid_provider_adapter", "provider fine-tuning adapter is malformed")
        return cast(FineTuningProviderAdapter, adapter)

    @staticmethod
    def _supports_fine_tuning(adapter: FineTuningProviderAdapter, model_id: str) -> bool:
        try:
            supported = adapter.supports_fine_tuning(model_id)
        except AdapterError as exc:
            raise ProviderError(exc.code, str(exc)) from exc
        except (AttributeError, TypeError) as exc:
            raise ProviderError(
                "invalid_provider_adapter",
                "provider fine-tuning adapter did not satisfy the capability contract",
            ) from exc
        if not isinstance(supported, bool):
            raise ProviderError(
                "invalid_provider_adapter",
                "provider fine-tuning capability result must be a boolean",
            )
        return supported


def _media_category(media_type: str) -> str:
    """Normalize MIME-like input to the catalog capability name."""

    if not isinstance(media_type, str):
        raise ProviderError("unsupported_media_category", "media type is not supported")
    normalized = media_type.strip().lower()
    category = normalized.split("/", 1)[0]
    if category not in {"image", "audio", "video"}:
        raise ProviderError("unsupported_media_category", "media type is not supported")
    return category


def _media_capability(media_category: str) -> str:
    """Map transport media categories to catalog capability names."""

    return "vision" if media_category == "image" else media_category
