"""Native Alibaba Cloud Model Studio (Qwen) fine-tuning adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from src.providers.base import (
    AdapterError,
    ChatRequest,
    ChatResponse,
    FineTuningRequest,
    FineTuningStatus,
    FineTuningSubmission,
    MediaAnalysisRequest,
    MediaAnalysisResult,
)
from src.providers.openai_compatible import OpenAICompatibleAdapter, OpenAICompatibleConfig
from src.providers.transport import (
    JsonRequestTransport,
    MultipartTransport,
    urllib_json_request_transport,
    urllib_multipart_transport,
)


@dataclass(frozen=True, slots=True)
class QwenFineTuningConfig:
    provider_id: str
    base_url: str
    api_key: str
    allowed_models: frozenset[str]
    fine_tuning_models: frozenset[str]
    chat_base_url: str | None = None
    timeout_seconds: float = 60.0
    n_epochs: int = 3
    batch_size: int = 1
    max_length: int = 8192
    split: float = 0.9
    media_capabilities: Mapping[str, frozenset[str]] = field(default_factory=dict)
    video_endpoint_path: str | None = None


class QwenFineTuningAdapter:
    """Chat adapter plus the native Qwen file/job lifecycle.

    Fine-tuning is opt-in per configured model.  A normal Qwen-compatible chat
    endpoint therefore never becomes trainable just because it has an API key.
    """

    def __init__(
        self,
        config: QwenFineTuningConfig,
        *,
        json_request: JsonRequestTransport | None = None,
        multipart_request: MultipartTransport | None = None,
        chat_transport=None,
    ):
        self.config = config
        self.provider_id = config.provider_id
        self.json_request = json_request or urllib_json_request_transport
        self.multipart_request = multipart_request or urllib_multipart_transport
        chat_base_url = config.chat_base_url or config.base_url
        self._chat = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                provider_id=config.provider_id,
                base_url=chat_base_url,
                api_key=config.api_key,
                allowed_models=config.allowed_models,
                timeout_seconds=config.timeout_seconds,
                media_capabilities=config.media_capabilities,
                video_endpoint_path=config.video_endpoint_path,
            ),
            transport=chat_transport,
        )

    def supports_model(self, model_id: str) -> bool:
        return model_id in self.config.allowed_models

    def chat(self, request: ChatRequest) -> ChatResponse:
        return self._chat.chat(request)

    def supports_media(self, model_id: str, media_category: str) -> bool:
        return self._chat.supports_media(model_id, media_category)

    def analyze_media(self, request: MediaAnalysisRequest) -> MediaAnalysisResult:
        return self._chat.analyze_media(request)

    def supports_fine_tuning(self, model_id: str) -> bool:
        return model_id in self.config.fine_tuning_models and self.supports_model(model_id)

    def submit_fine_tuning(self, request: FineTuningRequest) -> FineTuningSubmission:
        self._require_model(request.model_id)
        if request.provider_id != self.provider_id:
            raise AdapterError("invalid_provider_request", "provider request identity does not match adapter")
        if not request.dataset_path.is_file():
            raise AdapterError("dataset_unavailable", "training dataset is unavailable")
        upload = self.multipart_request(
            f"{self.config.base_url.rstrip('/')}/files",
            {"Authorization": f"Bearer {self.config.api_key}"},
            {"purpose": "fine-tune"},
            "files",
            request.dataset_path,
            self.config.timeout_seconds,
        )
        file_id = _uploaded_file_id(upload)
        try:
            payload = self.json_request(
                "POST",
                f"{self.config.base_url.rstrip('/')}/fine-tunes",
                self._headers(),
                {
                    "model": request.model_id,
                    "training_datasets": [{"data_source_type": "file_id", "file_id": file_id}],
                    "training_type": "sft",
                    # Qwen exposes this value in list responses, making a durable
                    # local job intent recoverable when the first write fails.
                    "job_name": request.job_id,
                    "hyper_parameters": {
                        "n_epochs": self.config.n_epochs,
                        "batch_size": self.config.batch_size,
                        "max_length": self.config.max_length,
                        "split": self.config.split,
                    },
                },
                self.config.timeout_seconds,
            )
        except AdapterError as exc:
            # A definitive HTTP rejection means no job was accepted. Timeout,
            # throttling, or transport loss is deliberately left recoverable:
            # the provider may have accepted the job before the response failed.
            if exc.code == "provider_http_error":
                self._best_effort_delete_training_file(file_id)
            raise
        try:
            output = _output(payload)
            provider_job_id = _required_string(output.get("job_id"), "provider job ID")
        except AdapterError:
            # A malformed success response is an uncertain handoff. Keep the
            # uploaded file until the durable job intent can be reconciled.
            raise
        return FineTuningSubmission(provider_job_id)

    def delete_training_file(self, file_id: str) -> None:
        """Delete an uploaded dataset after a failed task creation handoff."""

        self.json_request(
            "DELETE",
            f"{self.config.base_url.rstrip('/')}/files/{quote(file_id, safe='-_.')}",
            self._headers(),
            None,
            self.config.timeout_seconds,
        )

    def _best_effort_delete_training_file(self, file_id: str) -> None:
        try:
            self.delete_training_file(file_id)
        except AdapterError:
            # Preserve the original submission/response error. The failed
            # cleanup remains observable through provider-side retention tools.
            return

    def recover_fine_tuning_submission(self, client_job_id: str) -> FineTuningSubmission | None:
        payload = self.json_request(
            "GET",
            f"{self.config.base_url.rstrip('/')}/fine-tunes?page_no=1&page_size=1000",
            self._headers(),
            None,
            self.config.timeout_seconds,
        )
        output = _output(payload)
        jobs = output.get("jobs")
        total = output.get("total")
        if not isinstance(jobs, list) or not isinstance(total, int) or total < len(jobs):
            raise AdapterError("invalid_provider_response", "provider job listing is invalid")
        if total > len(jobs):
            raise AdapterError(
                "provider_reconciliation_incomplete",
                "provider job listing was truncated before reconciliation",
            )
        for job in jobs:
            if isinstance(job, Mapping) and job.get("job_name") == client_job_id:
                return FineTuningSubmission(_required_string(job.get("job_id"), "provider job ID"))
        return None

    def get_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        payload = self.json_request(
            "GET",
            f"{self.config.base_url.rstrip('/')}/fine-tunes/{quote(provider_job_id, safe='-_.')}",
            self._headers(),
            None,
            self.config.timeout_seconds,
        )
        return _status_from_output(_output(payload))

    def cancel_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        payload = self.json_request(
            "POST",
            f"{self.config.base_url.rstrip('/')}/fine-tunes/{quote(provider_job_id, safe='-_.')}/cancel",
            self._headers(),
            {},
            self.config.timeout_seconds,
        )
        output = _output(payload)
        if str(output.get("status", "")).casefold() not in {"success", "succeeded", "cancelled", "canceled"}:
            raise AdapterError("provider_cancellation_unconfirmed", "provider did not confirm cancellation")
        return FineTuningStatus(state="cancelled", progress_percent=0)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}

    def _require_model(self, model_id: str) -> None:
        if not self.supports_fine_tuning(model_id):
            raise AdapterError("capability_not_supported", "model does not support fine-tuning")


def _output(payload: Mapping[str, object]) -> Mapping[str, object]:
    output = payload.get("output")
    if not isinstance(output, Mapping):
        raise AdapterError("invalid_provider_response", "provider response has no output object")
    return output


def _uploaded_file_id(payload: Mapping[str, object]) -> str:
    data = payload.get("data")
    uploaded = data.get("uploaded_files") if isinstance(data, Mapping) else None
    if not isinstance(uploaded, list) or not uploaded or not isinstance(uploaded[0], Mapping):
        raise AdapterError("invalid_provider_response", "provider upload returned no file ID")
    return _required_string(uploaded[0].get("file_id"), "uploaded file ID")


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
        raise AdapterError("invalid_provider_response", f"provider response has no valid {label}")
    return value.strip()


def _status_from_output(output: Mapping[str, object]) -> FineTuningStatus:
    state = str(output.get("status", "")).casefold()
    if state in {"pending", "queuing", "queued", "validating"}:
        return FineTuningStatus(state="queued", progress_percent=_progress(output))
    if state in {"running", "training"}:
        return FineTuningStatus(state="running", progress_percent=_progress(output))
    if state in {"canceled", "cancelled", "canceling", "cancelling"}:
        return FineTuningStatus(state="cancelled", progress_percent=0)
    if state in {"failed", "error"}:
        # Only an explicit boolean from the provider may make a terminal error
        # retryable; truthy strings or numbers must not broaden retry behavior.
        return FineTuningStatus(state="failed", retryable=output.get("retryable") is True)
    if state in {"succeeded", "success", "completed", "complete"}:
        artifact_id = output.get("finetuned_output")
        evaluation: dict[str, object] = {}
        for key in ("usage", "output_cnt", "validation_metrics", "training_metrics", "metrics"):
            value = output.get(key)
            if value is not None:
                evaluation[key] = value
        return FineTuningStatus(
            state="completed",
            progress_percent=100,
            artifact_id=artifact_id if isinstance(artifact_id, str) and artifact_id else None,
            evaluation=evaluation or None,
        )
    raise AdapterError("invalid_provider_response", "provider returned an unknown training status")


def _progress(output: Mapping[str, object]) -> int | None:
    for key in ("progress", "progress_percent", "progress_percentage"):
        value = output.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100:
            return value
        if isinstance(value, float) and 0 <= value <= 100:
            return int(value)
    return None
