"""Deterministic adapter and metadata that can never be enabled outside test mode."""

from uuid import uuid4

from src.providers.base import (
    AdapterError,
    ChatRequest,
    ChatResponse,
    FineTuningRequest,
    FineTuningStatus,
    FineTuningSubmission,
)
from src.providers.catalog import ModelDefinition, ModelPricing, ProviderDefinition


def deterministic_test_provider_definition() -> ProviderDefinition:
    """Return the catalog entry used exclusively by the in-process test runtime.

    Test-mode training still exercises the same catalog capability and price gate as
    a real provider. Keeping this metadata next to the adapter prevents a default,
    development, or production catalog from accidentally advertising fake support.
    """

    return ProviderDefinition(
        id="test",
        display_name="Deterministic Test Provider",
        api_style="test",
        capabilities=("chat", "fine_tuning"),
        credential_mode="test",
        pricing_source="test",
        configured=True,
        models=(
            ModelDefinition(
                id="deterministic",
                display_name="Deterministic Test Model",
                capabilities=("text", "chat", "fine_tuning"),
                pricing_source="test",
                pricing=ModelPricing(
                    input_price_per_million_tokens=0.0,
                    output_price_per_million_tokens=0.0,
                    training_price_per_million_tokens=10.0,
                    currency="USD",
                    source="test",
                ),
            ),
        ),
    )


class DeterministicTestAdapter:
    provider_id = "test"

    def __init__(self) -> None:
        self.submissions: list[FineTuningRequest] = []
        self._cancelled_jobs: set[str] = set()

    def supports_model(self, model_id: str) -> bool:
        return model_id == "deterministic"

    def chat(self, request: ChatRequest) -> ChatResponse:
        last_message = request.messages[-1].content if request.messages else ""
        return ChatResponse(
            provider_id=self.provider_id,
            model_id=request.model_id,
            content=f"测试回复：{last_message}",
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            provider_request_id=f"test-request-{uuid4().hex}",
        )

    def supports_fine_tuning(self, model_id: str) -> bool:
        return self.supports_model(model_id)

    def submit_fine_tuning(self, request: FineTuningRequest) -> FineTuningSubmission:
        if not self.supports_fine_tuning(request.model_id):
            raise AdapterError("unknown_model", "test model is not available for fine-tuning")
        provider_job_id = f"test-ft-{request.job_id}"
        self.submissions.append(request)
        self._cancelled_jobs.discard(provider_job_id)
        return FineTuningSubmission(provider_job_id=provider_job_id)

    def recover_fine_tuning_submission(self, client_job_id: str) -> FineTuningSubmission | None:
        """Reconcile a prior deterministic handoff by its durable local job ID."""

        for request in self.submissions:
            if request.job_id == client_job_id:
                return FineTuningSubmission(provider_job_id=f"test-ft-{client_job_id}")
        return None

    def get_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        self._require_known_job(provider_job_id)
        if provider_job_id in self._cancelled_jobs:
            return FineTuningStatus(state="cancelled", progress_percent=0)
        return FineTuningStatus(
            state="completed",
            progress_percent=100,
            artifact_id=f"artifact-{provider_job_id}",
            evaluation={"status": "verified"},
        )

    def cancel_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        self._require_known_job(provider_job_id)
        self._cancelled_jobs.add(provider_job_id)
        return FineTuningStatus(state="cancelled", progress_percent=0)

    def _require_known_job(self, provider_job_id: str) -> None:
        if not any(f"test-ft-{request.job_id}" == provider_job_id for request in self.submissions):
            raise AdapterError("unknown_fine_tuning_job", "test fine-tuning job does not exist")
