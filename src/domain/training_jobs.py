"""Validated, durable metadata for provider-backed fine-tuning jobs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
import json
import math
import re
from types import MappingProxyType
from typing import Any


_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


class TrainingJobValidationError(ValueError):
    """A stable state or metadata failure before an API response is created."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TrainingJobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class TrainingJob:
    """Encrypted-persistence-safe job metadata with explicit lifecycle transitions.

    Dataset bytes and their temporary local path deliberately do not appear here.
    The persisted record is limited to auditable identifiers, counts, digests, cost,
    state, and a small verified evaluation summary returned by the provider.
    """

    id: str
    diagnostic_id: str
    persona_id: str
    import_id: str
    consent_id: str
    provider_id: str
    model_id: str
    dataset_sha256: str
    sample_count: int
    source_record_count: int
    source_record_digest: str
    estimated_tokens: int
    estimated_cost: float
    created_at: str
    updated_at: str
    state: TrainingJobState = TrainingJobState.PENDING
    provider_job_id: str | None = None
    # Set before the provider receives a dataset. It makes an uncertain handoff
    # recoverable after a process or persistence failure even before an opaque
    # provider job ID has been durably recorded.
    submission_started: bool = False
    progress_percent: int = 0
    failure_code: str | None = None
    # A plaintext cleanup failure is a local privacy event, not evidence that the
    # provider's actual lifecycle state changed. Keep it separate from provider
    # failures so a verified remote completion is never overwritten as failed.
    local_cleanup_failure_code: str | None = None
    retryable: bool = False
    artifact_id: str | None = None
    evaluation: Mapping[str, Any] | None = None
    # Repository-managed optimistic revision. It is encrypted with the job and
    # duplicated in SQLite only to reject stale writes without exposing content.
    revision: int = 0

    def __post_init__(self) -> None:
        for field_name, value in (
            ("job_id", self.id),
            ("diagnostic_id", self.diagnostic_id),
            ("persona_id", self.persona_id),
            ("import_id", self.import_id),
            ("consent_id", self.consent_id),
        ):
            _identifier(value, field_name)
        for field_name, value, maximum in (
            ("provider_id", self.provider_id, 128),
            ("model_id", self.model_id, 256),
            ("created_at", self.created_at, 128),
            ("updated_at", self.updated_at, 128),
        ):
            _text(value, field_name, maximum)
        _sha256(self.dataset_sha256, "dataset_sha256")
        _sha256(self.source_record_digest, "source_record_digest")
        _positive_int(self.sample_count, "sample_count")
        _positive_int(self.source_record_count, "source_record_count")
        _positive_int(self.estimated_tokens, "estimated_tokens")
        _cost(self.estimated_cost)
        _progress(self.progress_percent)
        revision = _revision(self.revision)
        if not isinstance(self.state, TrainingJobState):
            raise TrainingJobValidationError("invalid_training_state", "training state is invalid")
        if not isinstance(self.retryable, bool):
            raise TrainingJobValidationError("invalid_retryable", "retryable must be a boolean")
        if not isinstance(self.submission_started, bool):
            raise TrainingJobValidationError(
                "invalid_submission_started", "submission_started must be a boolean"
            )

        provider_job_id = _optional_text(self.provider_job_id, "provider_job_id", 512)
        failure_code = _optional_failure_code(self.failure_code)
        local_cleanup_failure_code = _optional_failure_code(self.local_cleanup_failure_code)
        artifact_id = _optional_text(self.artifact_id, "artifact_id", 512)
        evaluation = _optional_evaluation(self.evaluation)
        object.__setattr__(self, "provider_job_id", provider_job_id)
        object.__setattr__(self, "failure_code", failure_code)
        object.__setattr__(self, "local_cleanup_failure_code", local_cleanup_failure_code)
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "evaluation", evaluation)
        object.__setattr__(self, "revision", revision)
        self._validate_state_fields()

    @classmethod
    def pending(
        cls,
        *,
        job_id: object,
        diagnostic_id: object | None = None,
        persona_id: object,
        import_id: object,
        consent_id: object,
        provider_id: object,
        model_id: object,
        dataset_sha256: object,
        sample_count: object,
        source_record_count: object,
        source_record_digest: object,
        estimated_tokens: object,
        estimated_cost: object,
        created_at: object,
    ) -> "TrainingJob":
        timestamp = _text(created_at, "created_at", 128)
        return cls(
            id=_identifier(job_id, "job_id"),
            diagnostic_id=_identifier(
                diagnostic_id if diagnostic_id is not None else job_id,
                "diagnostic_id",
            ),
            persona_id=_identifier(persona_id, "persona_id"),
            import_id=_identifier(import_id, "import_id"),
            consent_id=_identifier(consent_id, "consent_id"),
            provider_id=_text(provider_id, "provider_id", 128),
            model_id=_text(model_id, "model_id", 256),
            dataset_sha256=_sha256(dataset_sha256, "dataset_sha256"),
            sample_count=_positive_int(sample_count, "sample_count"),
            source_record_count=_positive_int(source_record_count, "source_record_count"),
            source_record_digest=_sha256(source_record_digest, "source_record_digest"),
            estimated_tokens=_positive_int(estimated_tokens, "estimated_tokens"),
            estimated_cost=_cost(estimated_cost),
            created_at=timestamp,
            updated_at=timestamp,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TrainingJob":
        if not isinstance(value, Mapping):
            raise TrainingJobValidationError("invalid_training_job", "training job must be an object")
        try:
            state = TrainingJobState(value["state"])
        except KeyError as exc:
            raise TrainingJobValidationError(
                "missing_training_job_field", f"training job is missing {exc.args[0]}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise TrainingJobValidationError("invalid_training_state", "training state is invalid") from exc
        try:
            return cls(
                id=value["id"],
                diagnostic_id=value.get("diagnostic_id", value["id"]),
                persona_id=value["persona_id"],
                import_id=value["import_id"],
                consent_id=value["consent_id"],
                provider_id=value["provider_id"],
                model_id=value["model_id"],
                dataset_sha256=value["dataset_sha256"],
                sample_count=value["sample_count"],
                source_record_count=value["source_record_count"],
                source_record_digest=value["source_record_digest"],
                estimated_tokens=value["estimated_tokens"],
                estimated_cost=value["estimated_cost"],
                created_at=value["created_at"],
                updated_at=value["updated_at"],
                state=state,
                provider_job_id=value.get("provider_job_id"),
                submission_started=value.get(
                    "submission_started",
                    value.get("provider_job_id") is not None
                    or state in {TrainingJobState.RUNNING, TrainingJobState.COMPLETED},
                ),
                progress_percent=value.get("progress_percent", 0),
                failure_code=value.get("failure_code"),
                local_cleanup_failure_code=value.get("local_cleanup_failure_code"),
                retryable=value.get("retryable", False),
                artifact_id=value.get("artifact_id"),
                evaluation=value.get("evaluation"),
                revision=value.get("revision", 0),
            )
        except KeyError as exc:
            raise TrainingJobValidationError(
                "missing_training_job_field", f"training job is missing {exc.args[0]}"
            ) from exc

    def start_provider_submission(self, updated_at: object) -> "TrainingJob":
        """Durably mark that the provider may receive this dataset next.

        This transition precedes the external request. If the provider accepts but
        writing its opaque ID fails, cancellation and refresh can reconcile via the
        immutable local ``job_id`` instead of treating the job as never submitted.
        """

        self._require_open(TrainingJobState.PENDING)
        if self.submission_started:
            raise TrainingJobValidationError(
                "training_submission_already_started",
                "training provider submission was already started",
            )
        if self.provider_job_id is not None:
            raise TrainingJobValidationError(
                "training_submission_already_bound",
                "training job already has a provider submission reference",
            )
        return replace(
            self,
            submission_started=True,
            updated_at=_text(updated_at, "updated_at", 128),
        )

    def bind_provider_submission(self, provider_job_id: object, updated_at: object) -> "TrainingJob":
        """Durably retain the remote reference before exposing a running state.

        A remote provider can accept work while a later local write fails. Keeping
        the opaque provider ID on ``pending`` lets a subsequent cancellation or
        recovery attempt reach that remote job instead of silently losing it.
        """

        self._require_open(TrainingJobState.PENDING)
        if not self.submission_started:
            raise TrainingJobValidationError(
                "training_submission_not_started",
                "provider submission reference requires a durable handoff intent",
            )
        if self.provider_job_id is not None:
            raise TrainingJobValidationError(
                "training_submission_already_bound",
                "training job already has a provider submission reference",
            )
        return replace(
            self,
            provider_job_id=_text(provider_job_id, "provider_job_id", 512),
            updated_at=_text(updated_at, "updated_at", 128),
        )

    def mark_running(self, provider_job_id: object, updated_at: object) -> "TrainingJob":
        self._require_open(TrainingJobState.PENDING)
        if not self.submission_started:
            raise TrainingJobValidationError(
                "training_submission_not_started",
                "running training job requires a durable handoff intent",
            )
        provider_job = _text(provider_job_id, "provider_job_id", 512)
        if self.provider_job_id is not None and self.provider_job_id != provider_job:
            raise TrainingJobValidationError(
                "provider_job_reference_mismatch",
                "running job must retain its provider submission reference",
            )
        return replace(
            self,
            state=TrainingJobState.RUNNING,
            provider_job_id=provider_job,
            updated_at=_text(updated_at, "updated_at", 128),
            progress_percent=0,
        )

    def update_progress(self, progress_percent: object, updated_at: object) -> "TrainingJob":
        self._require_open(TrainingJobState.RUNNING)
        progress = _progress(progress_percent)
        if progress < self.progress_percent:
            raise TrainingJobValidationError(
                "training_progress_regression",
                "provider training progress cannot move backwards",
            )
        return replace(
            self,
            progress_percent=progress,
            updated_at=_text(updated_at, "updated_at", 128),
        )

    def complete(
        self,
        artifact_id: object,
        evaluation: object,
        updated_at: object,
    ) -> "TrainingJob":
        self._require_open(TrainingJobState.RUNNING)
        try:
            artifact = _text(artifact_id, "artifact_id", 512)
            verified_evaluation = _required_evaluation(evaluation)
        except TrainingJobValidationError as exc:
            raise TrainingJobValidationError(
                "training_result_unverified",
                "provider completion is missing a verified artifact or evaluation",
            ) from exc
        return replace(
            self,
            state=TrainingJobState.COMPLETED,
            artifact_id=artifact,
            evaluation=verified_evaluation,
            progress_percent=100,
            retryable=False,
            updated_at=_text(updated_at, "updated_at", 128),
        )

    def fail(
        self,
        failure_code: object,
        *,
        retryable: object,
        updated_at: object,
    ) -> "TrainingJob":
        self._require_open(TrainingJobState.PENDING, TrainingJobState.RUNNING)
        if not isinstance(retryable, bool):
            raise TrainingJobValidationError("invalid_retryable", "retryable must be a boolean")
        return replace(
            self,
            state=TrainingJobState.FAILED,
            failure_code=_required_failure_code(failure_code),
            retryable=retryable,
            updated_at=_text(updated_at, "updated_at", 128),
        )

    def cancel(self, updated_at: object) -> "TrainingJob":
        self._require_open(TrainingJobState.PENDING, TrainingJobState.RUNNING)
        return replace(
            self,
            state=TrainingJobState.CANCELLED,
            retryable=False,
            updated_at=_text(updated_at, "updated_at", 128),
        )

    def record_cleanup_failure(self, failure_code: object, updated_at: object) -> "TrainingJob":
        """Retain a local plaintext-cleanup failure without changing provider state."""

        return replace(
            self,
            local_cleanup_failure_code=_required_failure_code(failure_code),
            updated_at=_text(updated_at, "updated_at", 128),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "diagnostic_id": self.diagnostic_id,
            "persona_id": self.persona_id,
            "import_id": self.import_id,
            "consent_id": self.consent_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "dataset_sha256": self.dataset_sha256,
            "sample_count": self.sample_count,
            "source_record_count": self.source_record_count,
            "source_record_digest": self.source_record_digest,
            "estimated_tokens": self.estimated_tokens,
            "estimated_cost": self.estimated_cost,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "state": self.state.value,
            "provider_job_id": self.provider_job_id,
            "submission_started": self.submission_started,
            "progress_percent": self.progress_percent,
            "failure_code": self.failure_code,
            "local_cleanup_failure_code": self.local_cleanup_failure_code,
            "retryable": self.retryable,
            "artifact_id": self.artifact_id,
            "evaluation": dict(self.evaluation) if self.evaluation is not None else None,
            "revision": self.revision,
        }

    def _validate_state_fields(self) -> None:
        if self.provider_job_id is not None and not self.submission_started:
            raise TrainingJobValidationError(
                "invalid_training_state",
                "provider job metadata requires a durable handoff intent",
            )
        if self.state is TrainingJobState.PENDING:
            invalid = any(
                (
                    self.provider_job_id is not None and not self.submission_started,
                    self.failure_code is not None,
                    self.artifact_id is not None,
                    self.evaluation is not None,
                    self.retryable,
                    self.progress_percent != 0,
                )
            )
        elif self.state is TrainingJobState.RUNNING:
            invalid = any(
                (
                    self.provider_job_id is None,
                    not self.submission_started,
                    self.failure_code is not None,
                    self.artifact_id is not None,
                    self.evaluation is not None,
                    self.retryable,
                )
            )
        elif self.state is TrainingJobState.COMPLETED:
            invalid = any(
                (
                    self.provider_job_id is None,
                    not self.submission_started,
                    self.failure_code is not None,
                    self.artifact_id is None,
                    self.evaluation is None,
                    self.progress_percent != 100,
                    self.retryable,
                )
            )
        elif self.state is TrainingJobState.FAILED:
            invalid = any((self.failure_code is None, self.artifact_id is not None, self.evaluation is not None))
        else:
            invalid = any(
                (
                    self.failure_code is not None,
                    self.artifact_id is not None,
                    self.evaluation is not None,
                    self.retryable,
                )
            )
        if invalid:
            raise TrainingJobValidationError(
                "invalid_training_state",
                "training job metadata is incompatible with its lifecycle state",
            )

    def _require_open(self, *allowed: TrainingJobState) -> None:
        if self.state not in allowed:
            raise TrainingJobValidationError(
                "training_job_closed",
                "training job cannot transition from its current state",
            )


def _identifier(value: object, field_name: str) -> str:
    return _text(value, field_name, 128)


def _text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrainingJobValidationError(f"invalid_{field_name}", f"{field_name} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum or any(ord(character) < 32 for character in text):
        raise TrainingJobValidationError(f"invalid_{field_name}", f"{field_name} is not valid metadata")
    return text


def _optional_text(value: object, field_name: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _text(value, field_name, maximum)


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise TrainingJobValidationError(f"invalid_{field_name}", f"{field_name} must be a SHA-256 digest")
    return value


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TrainingJobValidationError(f"invalid_{field_name}", f"{field_name} must be positive")
    return value


def _progress(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise TrainingJobValidationError("invalid_training_progress", "training progress must be 0-100")
    return value


def _revision(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TrainingJobValidationError("invalid_training_revision", "training revision is invalid")
    return value


def _cost(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrainingJobValidationError("invalid_estimated_cost", "estimated_cost must be a number")
    cost = float(value)
    if not math.isfinite(cost) or cost < 0 or cost > 1_000_000_000:
        raise TrainingJobValidationError("invalid_estimated_cost", "estimated_cost is outside the supported range")
    return cost


def _required_failure_code(value: object) -> str:
    if not isinstance(value, str) or not _FAILURE_CODE.fullmatch(value):
        raise TrainingJobValidationError("invalid_failure_code", "failure_code is not a safe redacted code")
    return value


def _optional_failure_code(value: object) -> str | None:
    if value is None:
        return None
    return _required_failure_code(value)


def _required_evaluation(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise TrainingJobValidationError("invalid_evaluation", "evaluation must be a non-empty object")
    return _evaluation(value)


def _optional_evaluation(value: object) -> Mapping[str, Any] | None:
    if value is None:
        return None
    return _required_evaluation(value)


def _evaluation(value: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        # ``replace`` may feed this validator an existing MappingProxyType. Copy the
        # outer mapping before JSON normalization so immutable state transitions are
        # as valid as a provider's ordinary dict response.
        serialized = json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        parsed = json.loads(serialized)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TrainingJobValidationError("invalid_evaluation", "evaluation must be JSON metadata") from exc
    if not isinstance(parsed, dict) or not parsed or len(serialized.encode("utf-8")) > 8 * 1024:
        raise TrainingJobValidationError("invalid_evaluation", "evaluation is invalid or too large")
    if any(not isinstance(key, str) or not key.strip() or len(key) > 128 for key in parsed):
        raise TrainingJobValidationError("invalid_evaluation", "evaluation contains invalid keys")
    return MappingProxyType(parsed)
