"""Owner-scoped, consent-gated lifecycle for real provider fine-tuning jobs."""

from __future__ import annotations

from datetime import UTC, datetime
import re
import threading
from uuid import uuid4

from src.domain.consents import ConsentValidationError, MediaConsent
from src.domain.training_jobs import TrainingJob, TrainingJobState, TrainingJobValidationError
from src.providers.base import FineTuningRequest, FineTuningStatus
from src.providers.catalog import CatalogValidationError, ProviderCatalog
from src.providers.gateway import ProviderError, ProviderGateway
from src.services.consent_service import ConsentNotFoundError, ConsentService
from src.services.persona_service import PersonaNotFoundError, PersonaService
from src.services.training_dataset import TrainingDataset, TrainingDatasetBuilder, TrainingDatasetError
from src.services.training_repository import TrainingJobRepository, TrainingJobRepositoryError


_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_RETRYABLE_PROVIDER_CODES = frozenset(
    {"network_error", "provider_unavailable", "rate_limited", "server_error", "timeout"}
)
_OPEN_STATES = frozenset({TrainingJobState.PENDING, TrainingJobState.RUNNING})


class TrainingServiceError(RuntimeError):
    """A redacted service error with a correlation ID for support and audit lookup."""

    def __init__(self, code: str, message: str, *, diagnostic_id: str | None = None):
        super().__init__(message)
        self.code = _safe_failure_code(code, "training_service_unavailable")
        self.diagnostic_id = diagnostic_id or f"training-{uuid4()}"


class FineTuningService:
    """Submit, observe, and cancel training without inventing provider success.

    The service owns the order of operations because the temporary dataset contains
    approved persona text.  It proves local authorization and provider capability
    before creating that file, gives its path only to the gateway, and removes it
    before returning even when the remote provider rejects a submission.
    """

    def __init__(
        self,
        repository: TrainingJobRepository,
        datasets: TrainingDatasetBuilder,
        consents: ConsentService,
        catalog: ProviderCatalog,
        gateway: ProviderGateway,
        personas: PersonaService,
    ) -> None:
        self.repository = repository
        self.datasets = datasets
        self.consents = consents
        self.catalog = catalog
        self.gateway = gateway
        self.personas = personas
        # The local runtime is a single process but handles requests concurrently.
        # This lock protects the external handoff and all transitions against a
        # cancel/delete/status request observing a half-submitted job. SQLite
        # revisions remain the second line of defense for stale persisted copies.
        self._operation_lock = threading.RLock()

    def estimate(
        self,
        owner_id: str,
        persona_id: str,
        import_id: str,
        provider_id: str,
        model_id: str,
    ) -> dict[str, object]:
        """Calculate a local, redacted training estimate without provider transfer.

        This supports an informed-consent flow: a client can inspect the actual
        accepted-persona sample count and price before it creates the separate
        ``persona_text`` fine-tuning consent used by :meth:`create`.
        """

        self._require_persona(owner_id, persona_id)
        self._validate_provider(provider_id, model_id)
        dataset: TrainingDataset | None = None
        try:
            dataset = self._build_dataset(owner_id, persona_id, import_id)
            estimate = self._estimate_training_cost(provider_id, model_id, dataset.estimated_tokens)
            return {
                **estimate.to_dict(),
                "sample_count": dataset.sample_count,
                "dataset_sha256": dataset.sha256,
                "source_record_count": dataset.source_record_count,
                "source_record_digest": dataset.source_record_digest,
            }
        except TrainingDatasetError as exc:
            raise self._error(exc.code, "accepted persona training data could not be prepared") from exc
        except CatalogValidationError as exc:
            raise self._error(exc.code, "training price metadata is unavailable") from exc
        finally:
            if dataset is not None:
                self._cleanup_dataset(owner_id, None, dataset)

    def create(
        self,
        owner_id: str,
        persona_id: str,
        import_id: str,
        provider_id: str,
        model_id: str,
        consent_id: str,
    ) -> TrainingJob:
        """Create a durable job only after exact consent and capability checks.

        A provider submission failure is persisted as a redacted failed job.  This
        gives clients a durable retryability decision instead of treating a failed
        request as a locally fabricated successful training result.
        """

        self._require_persona(owner_id, persona_id)
        consent = self._authorize_training_consent(
            owner_id,
            persona_id,
            import_id,
            provider_id,
            model_id,
            consent_id,
        )
        self._validate_provider(provider_id, model_id)

        dataset: TrainingDataset | None = None
        job: TrainingJob | None = None
        try:
            dataset = self._build_dataset(owner_id, persona_id, import_id)
            estimate = self._estimate_training_cost(provider_id, model_id, dataset.estimated_tokens)
            self._require_authorized_cost(consent, estimate.estimated_cost)

            # Dataset creation can take time, so it intentionally happens before
            # the short serialized handoff. Recheck consent under the same lock
            # used by revoke() immediately before any provider sees its path.
            with self._operation_lock:
                with self.consents.provider_handoff_guard():
                    consent = self._authorize_training_consent(
                        owner_id,
                        persona_id,
                        import_id,
                        provider_id,
                        model_id,
                        consent_id,
                    )
                    self._require_authorized_cost(consent, estimate.estimated_cost)
                    self._require_unused_training_consent(owner_id, consent_id)
                    now = _timestamp()
                    job = TrainingJob.pending(
                        job_id=str(uuid4()),
                        diagnostic_id=f"training-{uuid4()}",
                        persona_id=persona_id,
                        import_id=import_id,
                        consent_id=consent_id,
                        provider_id=provider_id,
                        model_id=model_id,
                        dataset_sha256=dataset.sha256,
                        sample_count=dataset.sample_count,
                        source_record_count=dataset.source_record_count,
                        source_record_digest=dataset.source_record_digest,
                        estimated_tokens=dataset.estimated_tokens,
                        estimated_cost=estimate.estimated_cost,
                        created_at=now,
                    )
                    job = self._save(owner_id, job)
                    # This durable intent is written before the provider receives
                    # a plaintext path. It is the recovery key if the provider
                    # accepts work but persisting its opaque job ID later fails.
                    job = self._save(owner_id, job.start_provider_submission(_timestamp()))
                    request = FineTuningRequest(
                        provider_id=provider_id,
                        model_id=model_id,
                        job_id=job.id,
                        dataset_path=dataset.path,
                        dataset_sha256=dataset.sha256,
                        sample_count=dataset.sample_count,
                    )
                    try:
                        submission = self.gateway.submit_fine_tuning(request)
                        provider_job_id = _provider_job_id(submission.provider_job_id)
                    except ProviderError as exc:
                        recovered = self._recover_pending_submission(owner_id, job)
                        if recovered is None:
                            job = self._persist_failed_provider_error(owner_id, job, exc)
                            return job
                        job = recovered
                    except (AttributeError, TypeError, ValueError):
                        recovered = self._recover_pending_submission(owner_id, job)
                        if recovered is None:
                            job = self._persist_failed(
                                owner_id,
                                job,
                                "provider_submission_invalid",
                                retryable=False,
                            )
                            return job
                        job = recovered
                    else:
                        job = self._bind_provider_submission(owner_id, job, provider_job_id)
                    running = job.mark_running(job.provider_job_id, _timestamp())
                    try:
                        job = self._save(owner_id, running)
                    except TrainingServiceError:
                        self._compensate_submission_persistence(owner_id, job)
                        raise
                    return job
        except TrainingDatasetError as exc:
            raise self._error(exc.code, "accepted persona training data could not be prepared") from exc
        except CatalogValidationError as exc:
            raise self._error(exc.code, "training price metadata is unavailable") from exc
        except TrainingJobValidationError as exc:
            if job is not None and job.state in _OPEN_STATES:
                self._persist_failed(owner_id, job, exc.code, retryable=False)
            raise self._error(exc.code, "training job metadata is invalid") from exc
        finally:
            if dataset is not None:
                self._cleanup_dataset(owner_id, job, dataset)

    def get(self, owner_id: str, job_id: str) -> TrainingJob:
        try:
            job = self.repository.get(owner_id, job_id)
        except TrainingJobRepositoryError as exc:
            raise self._error(exc.code, "training job metadata is unavailable") from exc
        if job is None:
            raise self._error("training_job_not_found", "training job was not found")
        return job

    def list(self, owner_id: str, persona_id: str | None = None) -> list[TrainingJob]:
        if persona_id is not None:
            self._require_persona(owner_id, persona_id)
        try:
            return self.repository.list(owner_id, persona_id)
        except TrainingJobRepositoryError as exc:
            raise self._error(exc.code, "training job metadata is unavailable") from exc

    def refresh(self, owner_id: str, job_id: str) -> TrainingJob:
        """Apply a normalized provider status, requiring evidence before completion."""

        with self._operation_lock:
            job = self.get(owner_id, job_id)
            if job.state is TrainingJobState.PENDING and job.provider_job_id is None:
                recovered = self._recover_pending_submission(owner_id, job)
                if recovered is None:
                    if job.submission_started:
                        return self._persist_failed(
                            owner_id,
                            job,
                            "provider_submission_not_found",
                            retryable=False,
                        )
                    return job
                job = recovered
            if job.state not in {TrainingJobState.PENDING, TrainingJobState.RUNNING}:
                return job
            if job.provider_job_id is None:
                return self._persist_failed(
                    owner_id,
                    job,
                    "provider_job_reference_missing",
                    retryable=False,
                )
            try:
                status = self.gateway.get_fine_tuning_job(
                    job.provider_id,
                    job.model_id,
                    job.provider_job_id,
                )
            except ProviderError as exc:
                # A status lookup outage does not prove that a remote job failed.
                # Keep its durable state recoverable and surface a redacted error.
                raise self._error(
                    exc.code,
                    "provider training status is temporarily unavailable",
                    diagnostic_id=job.diagnostic_id,
                ) from exc
            return self._apply_status(owner_id, job, status)

    def cancel(self, owner_id: str, job_id: str) -> TrainingJob:
        """Record cancellation only after the remote provider confirms it."""

        with self._operation_lock:
            job = self.get(owner_id, job_id)
            if job.state in {TrainingJobState.CANCELLED, TrainingJobState.COMPLETED, TrainingJobState.FAILED}:
                return job
            if job.state is TrainingJobState.PENDING and job.provider_job_id is None:
                recovered = self._recover_pending_submission(owner_id, job)
                if recovered is None:
                    return self._save(owner_id, job.cancel(_timestamp()))
                job = recovered
            if job.provider_job_id is None:
                return self._persist_failed(
                    owner_id,
                    job,
                    "provider_job_reference_missing",
                    retryable=False,
                )
            try:
                status = self.gateway.cancel_fine_tuning_job(
                    job.provider_id,
                    job.model_id,
                    job.provider_job_id,
                )
            except ProviderError as exc:
                raise self._error(
                    exc.code,
                    "provider cancellation could not be confirmed",
                    diagnostic_id=job.diagnostic_id,
                ) from exc

            state = _provider_state(status)
            if state == "cancelled":
                return self._save(owner_id, job.cancel(_timestamp()))
            if state in {"completed", "failed"}:
                # A remote completion or failure can race a cancellation request.
                # Apply provider evidence rather than overwriting it with intent.
                return self._apply_status(owner_id, job, status)
            raise self._error(
                "provider_cancellation_unconfirmed",
                "provider did not confirm training cancellation",
                diagnostic_id=job.diagnostic_id,
            )

    def delete_for_persona(self, owner_id: str, persona_id: str) -> dict[str, int]:
        """Remove local records while explicitly counting external cleanup limits."""

        with self._operation_lock:
            self._require_persona(owner_id, persona_id)
            jobs = self.list(owner_id, persona_id)
            limitations = sum(
                1
                for job in jobs
                if job.provider_job_id is not None or job.submission_started
            )
            for job in jobs:
                if job.state in _OPEN_STATES:
                    try:
                        self.cancel(owner_id, job.id)
                    except TrainingServiceError:
                        # Local persona deletion must still complete. The response
                        # explicitly reports that remote cleanup could be limited.
                        pass
            try:
                deleted = self.repository.delete_for_persona(owner_id, persona_id)
            except TrainingJobRepositoryError as exc:
                raise self._error(exc.code, "training job metadata could not be removed") from exc
            return {
                "deleted_training_jobs": deleted,
                "external_training_cleanup_limitations": limitations,
            }

    def _apply_status(
        self,
        owner_id: str,
        job: TrainingJob,
        status: FineTuningStatus,
    ) -> TrainingJob:
        state = _provider_state(status)
        if state in {"pending", "queued", "running"}:
            try:
                progress = _progress_or_current(status, job.progress_percent)
            except TrainingServiceError as exc:
                # A malformed polling response is not evidence that the remote
                # training job failed. Keep the previous durable state so a later
                # valid poll can recover instead of fabricating terminal failure.
                raise self._error(
                    exc.code,
                    "provider training status is invalid",
                    diagnostic_id=job.diagnostic_id,
                ) from exc
            active = job
            if active.state is TrainingJobState.PENDING:
                if active.provider_job_id is None:
                    return self._persist_failed(
                        owner_id,
                        active,
                        "provider_job_reference_missing",
                        retryable=False,
                    )
                active = self._save(
                    owner_id,
                    active.mark_running(active.provider_job_id, _timestamp()),
                )
            updated = active.update_progress(progress, _timestamp())
            return self._save(owner_id, updated)
        if state == "completed":
            active = job
            if active.state is TrainingJobState.PENDING:
                if active.provider_job_id is None:
                    return self._persist_failed(
                        owner_id,
                        active,
                        "provider_job_reference_missing",
                        retryable=False,
                    )
                active = self._save(
                    owner_id,
                    active.mark_running(active.provider_job_id, _timestamp()),
                )
            try:
                completed = active.complete(status.artifact_id, status.evaluation, _timestamp())
            except TrainingJobValidationError:
                return self._persist_failed(
                    owner_id,
                    active,
                    "training_result_unverified",
                    retryable=False,
                )
            return self._save(owner_id, completed)
        if state == "failed":
            return self._persist_failed(
                owner_id,
                job,
                "provider_training_failed",
                retryable=_retryable_status(status),
            )
        if state == "cancelled":
            cancelled = job.cancel(_timestamp())
            return self._save(owner_id, cancelled)
        # Unknown state values are an upstream contract failure, not a verified
        # provider-side failure. Retain ``running`` for a subsequent safe retry.
        raise self._error(
            "provider_status_invalid",
            "provider training status is invalid",
            diagnostic_id=job.diagnostic_id,
        )

    def _authorize_training_consent(
        self,
        owner_id: str,
        persona_id: str,
        import_id: str,
        provider_id: str,
        model_id: str,
        consent_id: str,
    ) -> MediaConsent:
        try:
            consent = self.consents.authorize(
                owner_id,
                consent_id,
                provider_id=provider_id,
                model_id=model_id,
                data_category="persona_text",
                authorization_scope=f"fine_tuning:{import_id}",
            )
        except ConsentNotFoundError as exc:
            raise self._error("consent_not_found", "fine-tuning consent was not found") from exc
        except ConsentValidationError as exc:
            raise self._error(exc.code, "fine-tuning consent does not cover this request") from exc
        if consent.persona_id != persona_id or consent.purpose != "fine_tuning":
            raise self._error("consent_scope_mismatch", "fine-tuning consent does not cover this persona")
        return consent

    def _validate_provider(self, provider_id: str, model_id: str) -> None:
        try:
            self.gateway.validate_fine_tuning(provider_id, model_id)
        except ProviderError as exc:
            raise self._error(exc.code, "provider fine-tuning capability is unavailable") from exc

    def _build_dataset(self, owner_id: str, persona_id: str, import_id: str) -> TrainingDataset:
        return self.datasets.build(owner_id, persona_id, import_id)

    def _estimate_training_cost(self, provider_id: str, model_id: str, tokens: int):
        return self.catalog.estimate_training_cost(
            provider_id,
            model_id,
            training_tokens=tokens,
        )

    def _require_authorized_cost(self, consent: MediaConsent, estimated_cost: float) -> None:
        if estimated_cost > consent.estimated_cost:
            raise self._error(
                "training_cost_exceeds_consent",
                "estimated training cost exceeds the authorized limit",
            )

    def _require_unused_training_consent(self, owner_id: str, consent_id: str) -> None:
        """Make each cost-bound fine-tuning authorization a single submission.

        A consent records one user-approved ceiling for one import/provider/model
        scope. Reusing it would multiply external cost without another explicit
        confirmation, so even a failed durable submission requires a new consent.
        """

        try:
            already_used = any(job.consent_id == consent_id for job in self.repository.list(owner_id))
        except TrainingJobRepositoryError as exc:
            raise self._error(exc.code, "training job metadata is unavailable") from exc
        if already_used:
            raise self._error(
                "training_consent_already_used",
                "fine-tuning consent was already used for a submission",
            )

    def _require_persona(self, owner_id: str, persona_id: str) -> None:
        try:
            self.personas.get(owner_id, persona_id)
        except PersonaNotFoundError as exc:
            raise self._error("persona_not_found", "select an existing persona") from exc

    def _save(self, owner_id: str, job: TrainingJob) -> TrainingJob:
        try:
            return self.repository.save(owner_id, job)
        except TrainingJobRepositoryError as exc:
            raise self._error(
                exc.code,
                "training job metadata could not be persisted",
                diagnostic_id=job.diagnostic_id,
            ) from exc

    def _recover_pending_submission(
        self,
        owner_id: str,
        job: TrainingJob,
    ) -> TrainingJob | None:
        """Bind an accepted remote job after an uncertain local handoff.

        ``None`` is only accepted from adapters that can authoritatively prove the
        client-generated job ID was never accepted. A lookup outage leaves the
        durable pending intent untouched rather than locally cancelling an
        unknown remote job.
        """

        if job.state is not TrainingJobState.PENDING or job.provider_job_id is not None:
            return job
        if not job.submission_started:
            return None
        try:
            submission = self.gateway.recover_fine_tuning_submission(
                job.provider_id,
                job.model_id,
                job.id,
            )
        except ProviderError as exc:
            raise self._error(
                exc.code,
                "provider training submission could not be reconciled",
                diagnostic_id=job.diagnostic_id,
            ) from exc
        if submission is None:
            return None
        try:
            provider_job_id = _provider_job_id(submission.provider_job_id)
            return self._bind_provider_submission(owner_id, job, provider_job_id)
        except (AttributeError, TypeError, ValueError, TrainingJobValidationError) as exc:
            raise self._error(
                "provider_submission_invalid",
                "provider training reconciliation returned invalid metadata",
                diagnostic_id=job.diagnostic_id,
            ) from exc

    def _bind_provider_submission(
        self,
        owner_id: str,
        job: TrainingJob,
        provider_job_id: str,
    ) -> TrainingJob:
        """Persist a provider reference, compensating if the write fails."""

        submitted = job.bind_provider_submission(provider_job_id, _timestamp())
        try:
            return self._save(owner_id, submitted)
        except TrainingServiceError:
            self._compensate_submission_persistence(owner_id, submitted)
            raise

    def _persist_failed_provider_error(
        self,
        owner_id: str,
        job: TrainingJob,
        error: ProviderError,
    ) -> TrainingJob:
        code = _safe_failure_code(error.code, "provider_training_failed")
        return self._persist_failed(
            owner_id,
            job,
            code,
            retryable=code in _RETRYABLE_PROVIDER_CODES,
        )

    def _persist_failed(
        self,
        owner_id: str,
        job: TrainingJob,
        code: str,
        *,
        retryable: bool,
    ) -> TrainingJob:
        failed = job.fail(
            _safe_failure_code(code, "training_service_unavailable"),
            retryable=retryable,
            updated_at=_timestamp(),
        )
        return self._save(owner_id, failed)

    def _compensate_submission_persistence(self, owner_id: str, submitted: TrainingJob) -> None:
        """Best-effort cancel after a provider accepted work but local save failed.

        The pending record already carries the opaque provider reference when this
        method handles a failed running transition. For a failed first reference
        write, the earlier durable handoff intent supports later reconciliation by
        the provider's client job ID even when direct cancellation also fails.
        """

        provider_job_id = submitted.provider_job_id
        if provider_job_id is None:
            return
        try:
            status = self.gateway.cancel_fine_tuning_job(
                submitted.provider_id,
                submitted.model_id,
                provider_job_id,
            )
        except ProviderError:
            return
        if _provider_state(status) != "cancelled":
            return
        try:
            self._save(owner_id, submitted.cancel(_timestamp()))
        except TrainingServiceError:
            # This is deliberately best effort: surfacing the original persistence
            # error is safer than masking it as a confirmed local cancellation.
            return

    def _cleanup_dataset(
        self,
        owner_id: str,
        job: TrainingJob | None,
        dataset: TrainingDataset,
    ) -> None:
        try:
            dataset.cleanup()
        except TrainingDatasetError as exc:
            # A local plaintext cleanup failure outranks a normal return. If a
            # provider already received the file, request cancellation best effort
            # and record a redacted local failure before surfacing the condition.
            # ``job`` can be stale after a provider rejection or compensation has
            # advanced its encrypted revision. Re-read it first so this privacy
            # error is never replaced by an optimistic-lock conflict.
            active_job = job
            if job is not None:
                try:
                    active_job = self.get(owner_id, job.id)
                except TrainingServiceError:
                    # A concurrent persona deletion or storage outage must not
                    # hide a known plaintext-cleanup failure from the caller.
                    active_job = job
            if active_job is not None:
                if (
                    active_job.state is TrainingJobState.PENDING
                    and active_job.provider_job_id is None
                ):
                    try:
                        recovered = self._recover_pending_submission(owner_id, active_job)
                    except TrainingServiceError:
                        # The normal cancel/refresh path will retry provider
                        # reconciliation. Do not locally close an uncertain handoff.
                        recovered = None
                    if recovered is not None:
                        active_job = recovered
                if active_job.state in _OPEN_STATES and active_job.provider_job_id is not None:
                    try:
                        status = self.gateway.cancel_fine_tuning_job(
                            active_job.provider_id,
                            active_job.model_id,
                            active_job.provider_job_id,
                        )
                    except ProviderError:
                        pass
                    else:
                        try:
                            # A remote completion/failure can race cleanup. Its
                            # verified evidence is authoritative and must survive
                            # the local plaintext-cleanup error.
                            active_job = self._apply_status(owner_id, active_job, status)
                        except TrainingServiceError:
                            pass
                try:
                    self._save(
                        owner_id,
                        active_job.record_cleanup_failure(
                            "training_dataset_cleanup_failed",
                            _timestamp(),
                        ),
                    )
                except TrainingServiceError:
                    # Cleanup evidence remains the primary response even when a
                    # concurrent transition or storage outage prevents recording
                    # the extra redacted failure metadata.
                    pass
            raise self._error(
                exc.code,
                "temporary training data could not be removed",
                diagnostic_id=job.diagnostic_id if job is not None else None,
            ) from exc

    @staticmethod
    def _error(
        code: str,
        message: str,
        *,
        diagnostic_id: str | None = None,
    ) -> TrainingServiceError:
        return TrainingServiceError(code, message, diagnostic_id=diagnostic_id)


def _provider_job_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 512:
        raise ValueError("provider fine-tuning submission did not return a job ID")
    return value.strip()


def _provider_state(status: object) -> str:
    state = getattr(status, "state", None)
    if not isinstance(state, str) or not state.strip():
        return "invalid"
    return state.strip().casefold()


def _progress_or_current(status: object, current: int) -> int:
    value = getattr(status, "progress_percent", None)
    if value is None:
        return current
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise TrainingServiceError("provider_status_invalid", "provider training progress is invalid")
    return max(current, value)


def _retryable_status(status: object) -> bool:
    return getattr(status, "retryable", False) is True


def _safe_failure_code(value: object, fallback: str) -> str:
    if isinstance(value, str) and _FAILURE_CODE.fullmatch(value):
        return value
    return fallback


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()
