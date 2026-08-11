"""P2-07 owner-scoped fine-tuning preflight and provider lifecycle tests."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.providers.catalog import (
    ModelDefinition,
    ModelPricing,
    ProviderCatalog,
    ProviderDefinition,
)
from src.providers.gateway import ProviderError, ProviderGateway
from src.providers.base import FineTuningStatus
from src.providers.testing import DeterministicTestAdapter
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.consent_repository import ConsentRepository
from src.services.consent_service import ConsentService
from src.services.import_repository import ImportRepository
from src.services.import_service import ImportService
from src.services.local_auth import LocalAuthService
from src.services.master_key import (
    EnvironmentMasterKeyProvider,
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
)
from src.services.persona_repository import PersonaRepository
from src.services.persona_service import PersonaService
from src.services.storage import StorageLayout
from src.services.training_dataset import TrainingDatasetBuilder, TrainingDatasetError
from src.services.training_repository import TrainingJobRepository
from src.services.training_repository import TrainingJobRepositoryError
from src.services.training_service import FineTuningService, TrainingServiceError
from src.services.upload_service import UploadService


class FineTuningServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"s" * MASTER_KEY_BYTES).decode("ascii")
        encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(self.layout.database_path(), encryption, mode="test")
        self.owner_id = self.auth.owner_id
        self.personas = PersonaService(PersonaRepository(self.layout.database_path(), encryption))
        self.persona = self.personas.create(self.owner_id, "小雨", "friend")
        imports = ImportService(ImportRepository(self.layout.database_path(), encryption), self.personas)
        self.uploads = UploadService(self.layout, imports, encryption, read_block_bytes=8)
        self.datasets = TrainingDatasetBuilder(self.layout, self.uploads)
        self.consents = ConsentService(
            ConsentRepository(self.layout.database_path(), encryption), self.personas
        )
        self.repository = TrainingJobRepository(self.layout.database_path(), encryption)
        self.adapter = DeterministicTestAdapter()
        self.catalog = self._catalog()
        self.gateway = ProviderGateway(self.catalog, "test", {"test": self.adapter})
        self.service = FineTuningService(
            self.repository,
            self.datasets,
            self.consents,
            self.catalog,
            self.gateway,
            self.personas,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _catalog() -> ProviderCatalog:
        return ProviderCatalog(
            (
                ProviderDefinition(
                    id="test",
                    display_name="Deterministic Test",
                    api_style="test",
                    capabilities=("fine_tuning",),
                    credential_mode="test",
                    pricing_source="test",
                    models=(
                        ModelDefinition(
                            id="deterministic",
                            display_name="Deterministic",
                            capabilities=("fine_tuning",),
                            pricing=ModelPricing(
                                training_price_per_million_tokens=10.0,
                                currency="USD",
                                source="test",
                            ),
                        ),
                    ),
                    configured=True,
                ),
            )
        )

    @staticmethod
    def _digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def _completed_import(self) -> str:
        payload = b"".join(
            json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
            for message in (
                {"sender": "persona", "message": "第一条人物消息", "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "persona", "message": "第二条人物消息", "timestamp": "2026-08-11T10:01:00+08:00"},
            )
        )
        job = self.uploads.imports.create(
            self.owner_id,
            self.persona.id,
            "chat.jsonl",
            len(payload),
            "application/x-ndjson",
        )
        self.uploads.put_chunk(
            self.owner_id,
            job.id,
            0,
            len(payload),
            self._digest(payload),
            io.BytesIO(payload),
        )
        self.uploads.complete(self.owner_id, job.id, self._digest(payload))
        self.uploads.set_participant_mapping(self.owner_id, job.id, {"persona": "persona"})
        preview = self.uploads.preview(self.owner_id, job.id, max_records=100)
        self.uploads.save_corrections(
            self.owner_id,
            job.id,
            [
                {"record_id": record["record_id"], "review_state": "accepted", "fields": {}}
                for record in preview["records"]
            ],
        )
        return job.id

    def _consent(
        self,
        import_id: str,
        *,
        provider_id: str = "test",
        model_id: str = "deterministic",
        purpose: str = "fine_tuning",
        cost: float = 1.0,
    ):
        return self.consents.create(
            self.owner_id,
            self.persona.id,
            provider_id,
            model_id,
            "persona_text",
            cost,
            purpose,
            f"fine_tuning:{import_id}",
        )

    def test_submits_only_after_exact_preflight_and_completes_on_verified_refresh(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)

        running = self.service.create(
            self.owner_id,
            self.persona.id,
            import_id,
            "test",
            "deterministic",
            consent.id,
        )

        self.assertEqual("running", running.state.value)
        self.assertEqual(1, len(self.adapter.submissions))
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))
        completed = self.service.refresh(self.owner_id, running.id)
        self.assertEqual("completed", completed.state.value)
        self.assertTrue(completed.artifact_id)
        self.assertEqual("verified", completed.evaluation["status"])

    def test_rejects_wrong_training_purpose_before_building_a_dataset(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id, purpose="media_analysis")

        with patch.object(self.datasets, "build", side_effect=AssertionError("must not build")):
            with self.assertRaises(TrainingServiceError) as captured:
                self.service.create(
                    self.owner_id,
                    self.persona.id,
                    import_id,
                    "test",
                    "deterministic",
                    consent.id,
                )

        self.assertEqual("consent_scope_mismatch", captured.exception.code)
        self.assertEqual([], self.adapter.submissions)

    def test_provider_capability_failure_prevents_dataset_build(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(
            import_id,
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
        )
        unsupported_catalog = ProviderCatalog.default()
        unsupported = FineTuningService(
            self.repository,
            self.datasets,
            self.consents,
            unsupported_catalog,
            ProviderGateway(unsupported_catalog, "development", {}),
            self.personas,
        )

        with patch.object(self.datasets, "build", side_effect=AssertionError("must not build")):
            with self.assertRaises(TrainingServiceError) as captured:
                unsupported.create(
                    self.owner_id,
                    self.persona.id,
                    import_id,
                    "deepseek",
                    "deepseek-v4-flash",
                    consent.id,
                )

        self.assertEqual("capability_not_supported", captured.exception.code)

    def test_cancel_and_persona_cleanup_do_not_claim_provider_artifact_deletion(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)
        running = self.service.create(
            self.owner_id,
            self.persona.id,
            import_id,
            "test",
            "deterministic",
            consent.id,
        )

        cancelled = self.service.cancel(self.owner_id, running.id)
        self.assertEqual("cancelled", cancelled.state.value)
        result = self.service.delete_for_persona(self.owner_id, self.persona.id)
        self.assertEqual(1, result["deleted_training_jobs"])
        self.assertEqual(1, result["external_training_cleanup_limitations"])
        self.assertEqual([], self.repository.list(self.owner_id, self.persona.id))

    def test_cost_cap_prevents_provider_handoff_and_removes_temporary_dataset(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id, cost=0.0)

        with self.assertRaises(TrainingServiceError) as captured:
            self.service.create(
                self.owner_id,
                self.persona.id,
                import_id,
                "test",
                "deterministic",
                consent.id,
            )

        self.assertEqual("training_cost_exceeds_consent", captured.exception.code)
        self.assertEqual([], self.adapter.submissions)
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))

    def test_estimate_returns_redacted_dataset_metadata_without_provider_handoff(self) -> None:
        import_id = self._completed_import()

        estimate = self.service.estimate(
            self.owner_id,
            self.persona.id,
            import_id,
            "test",
            "deterministic",
        )

        self.assertEqual("test", estimate["provider_id"])
        self.assertEqual(2, estimate["sample_count"])
        self.assertGreater(estimate["training_tokens"], 0)
        self.assertRegex(estimate["dataset_sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual([], self.adapter.submissions)
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))

    def test_provider_completion_without_verified_output_is_persisted_as_failed(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)
        running = self.service.create(
            self.owner_id,
            self.persona.id,
            import_id,
            "test",
            "deterministic",
            consent.id,
        )

        with patch.object(
            self.adapter,
            "get_fine_tuning_job",
            return_value=FineTuningStatus(state="completed", progress_percent=100),
        ):
            failed = self.service.refresh(self.owner_id, running.id)

        self.assertEqual("failed", failed.state.value)
        self.assertEqual("training_result_unverified", failed.failure_code)

    def test_active_training_consent_cannot_be_reused_for_another_submission(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)
        first = self.service.create(
            self.owner_id,
            self.persona.id,
            import_id,
            "test",
            "deterministic",
            consent.id,
        )

        with self.assertRaises(TrainingServiceError) as captured:
            self.service.create(
                self.owner_id,
                self.persona.id,
                import_id,
                "test",
                "deterministic",
                consent.id,
            )

        self.assertEqual("running", first.state.value)
        self.assertEqual("training_consent_already_used", captured.exception.code)
        self.assertEqual(1, len(self.adapter.submissions))

    def test_consent_revocation_waits_until_provider_handoff_finishes(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)
        submit_entered = threading.Event()
        release_submit = threading.Event()
        revoke_finished = threading.Event()
        outcomes: dict[str, object] = {}
        original_submit = self.gateway.submit_fine_tuning

        def block_submit(request):
            submit_entered.set()
            if not release_submit.wait(timeout=5):
                raise AssertionError("test did not release provider submission")
            return original_submit(request)

        def create_job() -> None:
            try:
                outcomes["job"] = self.service.create(
                    self.owner_id,
                    self.persona.id,
                    import_id,
                    "test",
                    "deterministic",
                    consent.id,
                )
            except BaseException as exc:
                outcomes["create_error"] = exc

        def revoke_consent() -> None:
            try:
                outcomes["consent"] = self.consents.revoke(self.owner_id, consent.id)
            except BaseException as exc:
                outcomes["revoke_error"] = exc
            finally:
                revoke_finished.set()

        with patch.object(self.gateway, "submit_fine_tuning", side_effect=block_submit):
            create_thread = threading.Thread(target=create_job)
            create_thread.start()
            self.assertTrue(submit_entered.wait(timeout=2), "provider submission did not begin")
            revoke_thread = threading.Thread(target=revoke_consent)
            revoke_thread.start()
            self.assertFalse(
                revoke_finished.wait(timeout=0.2),
                "consent revocation raced an active provider handoff",
            )
            release_submit.set()
            create_thread.join(timeout=5)
            revoke_thread.join(timeout=5)

        self.assertFalse(create_thread.is_alive())
        self.assertFalse(revoke_thread.is_alive())
        self.assertNotIn("create_error", outcomes)
        self.assertNotIn("revoke_error", outcomes)
        self.assertEqual("running", outcomes["job"].state.value)
        self.assertEqual("revoked", outcomes["consent"].status)

    def test_cancel_waits_for_submission_then_cancels_the_bound_provider_job(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)
        submit_entered = threading.Event()
        release_submit = threading.Event()
        cancel_finished = threading.Event()
        outcomes: dict[str, object] = {}
        original_submit = self.gateway.submit_fine_tuning

        def block_submit(request):
            submit_entered.set()
            if not release_submit.wait(timeout=5):
                raise AssertionError("test did not release provider submission")
            return original_submit(request)

        def create_job() -> None:
            try:
                outcomes["created"] = self.service.create(
                    self.owner_id,
                    self.persona.id,
                    import_id,
                    "test",
                    "deterministic",
                    consent.id,
                )
            except BaseException as exc:
                outcomes["create_error"] = exc

        def cancel_job(job_id: str) -> None:
            try:
                outcomes["cancelled"] = self.service.cancel(self.owner_id, job_id)
            except BaseException as exc:
                outcomes["cancel_error"] = exc
            finally:
                cancel_finished.set()

        with patch.object(self.gateway, "submit_fine_tuning", side_effect=block_submit):
            create_thread = threading.Thread(target=create_job)
            create_thread.start()
            self.assertTrue(submit_entered.wait(timeout=2), "provider submission did not begin")
            pending = self.repository.list(self.owner_id, self.persona.id)
            self.assertEqual(1, len(pending))
            cancel_thread = threading.Thread(target=cancel_job, args=(pending[0].id,))
            cancel_thread.start()
            self.assertFalse(
                cancel_finished.wait(timeout=0.2),
                "cancellation observed a half-submitted training job",
            )
            release_submit.set()
            create_thread.join(timeout=5)
            cancel_thread.join(timeout=5)

        self.assertFalse(create_thread.is_alive())
        self.assertFalse(cancel_thread.is_alive())
        self.assertNotIn("create_error", outcomes)
        self.assertNotIn("cancel_error", outcomes)
        self.assertEqual("running", outcomes["created"].state.value)
        self.assertEqual("cancelled", outcomes["cancelled"].state.value)
        self.assertEqual("cancelled", self.service.get(self.owner_id, pending[0].id).state.value)

    def test_running_write_failure_cancels_and_keeps_provider_reference_recoverable(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)
        original_save = self.repository.save

        def fail_running_save(owner_id, job):
            if job.state.value == "running":
                raise TrainingJobRepositoryError("training_storage_unavailable", "database unavailable")
            return original_save(owner_id, job)

        with patch.object(self.repository, "save", side_effect=fail_running_save):
            with self.assertRaises(TrainingServiceError) as captured:
                self.service.create(
                    self.owner_id,
                    self.persona.id,
                    import_id,
                    "test",
                    "deterministic",
                    consent.id,
                )

        self.assertEqual("training_storage_unavailable", captured.exception.code)
        jobs = self.repository.list(self.owner_id, self.persona.id)
        self.assertEqual(1, len(jobs))
        self.assertEqual("cancelled", jobs[0].state.value)
        self.assertTrue(jobs[0].provider_job_id)
        self.assertTrue(self.adapter._cancelled_jobs)

    def test_invalid_provider_status_leaves_running_job_recoverable(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)
        running = self.service.create(
            self.owner_id,
            self.persona.id,
            import_id,
            "test",
            "deterministic",
            consent.id,
        )

        with patch.object(
            self.adapter,
            "get_fine_tuning_job",
            return_value=FineTuningStatus(state="nonsense"),
        ):
            with self.assertRaises(TrainingServiceError) as captured:
                self.service.refresh(self.owner_id, running.id)

        self.assertEqual("provider_status_invalid", captured.exception.code)
        self.assertEqual("running", self.service.get(self.owner_id, running.id).state.value)

    def test_cancel_recovers_remote_job_after_reference_write_and_compensation_failures(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)
        original_save = self.repository.save

        def fail_reference_save(owner_id, job):
            if job.state.value == "pending" and job.provider_job_id is not None:
                raise TrainingJobRepositoryError("training_storage_unavailable", "database unavailable")
            return original_save(owner_id, job)

        with (
            patch.object(self.repository, "save", side_effect=fail_reference_save),
            patch.object(
                self.gateway,
                "cancel_fine_tuning_job",
                side_effect=ProviderError("provider_unavailable", "provider unavailable"),
            ),
        ):
            with self.assertRaises(TrainingServiceError) as captured:
                self.service.create(
                    self.owner_id,
                    self.persona.id,
                    import_id,
                    "test",
                    "deterministic",
                    consent.id,
                )

        self.assertEqual("training_storage_unavailable", captured.exception.code)
        pending = self.repository.list(self.owner_id, self.persona.id)
        self.assertEqual(1, len(pending))
        self.assertTrue(pending[0].submission_started)
        self.assertIsNone(pending[0].provider_job_id)

        cancelled = self.service.cancel(self.owner_id, pending[0].id)

        self.assertEqual("cancelled", cancelled.state.value)
        self.assertEqual(
            "cancelled",
            self.adapter.get_fine_tuning_job(f"test-ft-{pending[0].id}").state,
        )

    def test_cleanup_failure_preserves_verified_provider_completion(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)
        completed_status = FineTuningStatus(
            state="completed",
            progress_percent=100,
            artifact_id="artifact-completed-during-cleanup",
            evaluation={"status": "verified"},
        )

        with (
            patch(
                "src.services.training_dataset.TrainingDataset.cleanup",
                side_effect=TrainingDatasetError(
                    "training_dataset_cleanup_failed",
                    "temporary dataset could not be removed",
                ),
            ),
            patch.object(self.gateway, "cancel_fine_tuning_job", return_value=completed_status),
        ):
            with self.assertRaises(TrainingServiceError) as captured:
                self.service.create(
                    self.owner_id,
                    self.persona.id,
                    import_id,
                    "test",
                    "deterministic",
                    consent.id,
                )

        self.assertEqual("training_dataset_cleanup_failed", captured.exception.code)
        job = self.repository.list(self.owner_id, self.persona.id)[0]
        self.assertEqual("completed", job.state.value)
        self.assertEqual("training_dataset_cleanup_failed", job.local_cleanup_failure_code)

    def test_provider_failure_does_not_mask_dataset_cleanup_failure(self) -> None:
        import_id = self._completed_import()
        consent = self._consent(import_id)

        with (
            patch.object(
                self.gateway,
                "submit_fine_tuning",
                side_effect=ProviderError("provider_unavailable", "provider unavailable"),
            ),
            patch(
                "src.services.training_dataset.TrainingDataset.cleanup",
                side_effect=TrainingDatasetError(
                    "training_dataset_cleanup_failed",
                    "temporary dataset could not be removed",
                ),
            ),
        ):
            with self.assertRaises(TrainingServiceError) as captured:
                self.service.create(
                    self.owner_id,
                    self.persona.id,
                    import_id,
                    "test",
                    "deterministic",
                    consent.id,
                )

        self.assertEqual("training_dataset_cleanup_failed", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
