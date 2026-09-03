"""Transport-neutral provider request and response types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class ChatRequest:
    provider_id: str
    model_id: str
    messages: tuple[ChatMessage, ...]
    temperature: float | None = None


@dataclass(frozen=True, slots=True)
class ChatResponse:
    provider_id: str
    model_id: str
    content: str
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    provider_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class MediaAnalysisRequest:
    """Provider-neutral handoff for one already-authorized media payload.

    The request deliberately carries only a controlled temporary path and
    bounded metadata.  Consent, ownership, and payload-size checks belong to
    the service layer before this boundary is reached.
    """

    provider_id: str
    model_id: str
    media_type: str
    media_path: Path
    prompt: str
    analysis_kind: str = "description"


@dataclass(frozen=True, slots=True)
class MediaAnalysisResult:
    """Normalized media result that never exposes provider response details."""

    provider_id: str
    model_id: str
    media_type: str
    description: str
    usage: dict[str, int] | None = None
    provider_request_id: str | None = None
    structured_data: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class FineTuningRequest:
    """Provider-neutral dataset handoff for a previously validated training job."""

    provider_id: str
    model_id: str
    job_id: str
    dataset_path: Path
    dataset_sha256: str
    sample_count: int


@dataclass(frozen=True, slots=True)
class FineTuningSubmission:
    """Opaque provider job reference returned after the provider accepts a job."""

    provider_job_id: str


@dataclass(frozen=True, slots=True)
class FineTuningStatus:
    """Normalized provider status; result verification remains a service concern."""

    state: str
    progress_percent: int | None = None
    artifact_id: str | None = None
    evaluation: Mapping[str, Any] | None = None
    retryable: bool = False


class ProviderAdapter(Protocol):
    provider_id: str

    def supports_model(self, model_id: str) -> bool:
        ...

    def chat(self, request: ChatRequest) -> ChatResponse:
        ...


@runtime_checkable
class FineTuningProviderAdapter(Protocol):
    """Optional extension implemented only by adapters that can really train a model.

    ``recover_fine_tuning_submission`` is intentionally mandatory. Providers may
    accept a request just before local persistence fails, so a client-generated
    ``job_id`` must be usable as an idempotency/reconciliation key before this
    service is allowed to hand it a plaintext dataset.
    """

    provider_id: str

    def supports_fine_tuning(self, model_id: str) -> bool:
        ...

    def submit_fine_tuning(self, request: FineTuningRequest) -> FineTuningSubmission:
        ...

    def recover_fine_tuning_submission(self, client_job_id: str) -> FineTuningSubmission | None:
        """Return the accepted job for ``client_job_id`` or authoritative absence."""
        ...

    def get_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        ...

    def cancel_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        ...


@runtime_checkable
class MediaAnalysisProviderAdapter(Protocol):
    """Optional extension for providers that can analyze uploaded media."""

    provider_id: str

    def supports_media(self, model_id: str, media_category: str) -> bool:
        ...

    def analyze_media(self, request: MediaAnalysisRequest) -> MediaAnalysisResult:
        ...


JsonObject = dict[str, Any]
