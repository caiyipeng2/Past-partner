"""P2-07 encrypted owner-scoped persistence for fine-tuning jobs."""

from __future__ import annotations

import base64
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.training_jobs import TrainingJob
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.local_auth import LocalAuthService
from src.services.master_key import (
    EnvironmentMasterKeyProvider,
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
)
from src.services.persona_repository import PersonaRepository
from src.services.persona_service import PersonaService
from src.services.storage import StorageLayout
from src.services.training_repository import TrainingJobRepository, TrainingJobRepositoryError


class TrainingJobRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"r" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(self.layout.database_path(), self.encryption, mode="test")
        self.owner_id = self.auth.owner_id
        self.personas = PersonaService(PersonaRepository(self.layout.database_path(), self.encryption))
        self.persona = self.personas.create(self.owner_id, "小雨", "friend")
        self.repository = TrainingJobRepository(self.layout.database_path(), self.encryption)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _job(self, job_id: str = "job-1") -> TrainingJob:
        return TrainingJob.pending(
            job_id=job_id,
            persona_id=self.persona.id,
            import_id="import-1",
            consent_id="consent-1",
            provider_id="test",
            model_id="deterministic",
            dataset_sha256="a" * 64,
            sample_count=2,
            source_record_count=2,
            source_record_digest="b" * 64,
            estimated_tokens=12,
            estimated_cost=0.25,
            created_at="2026-08-11T10:00:00+00:00",
        )

    def test_repository_encrypts_dataset_metadata_and_enforces_owner_scope(self) -> None:
        job = self._job()

        stored = self.repository.save(self.owner_id, job)

        self.assertEqual(1, stored.revision)
        self.assertEqual(stored, self.repository.get(self.owner_id, job.id))
        self.assertIsNone(self.repository.get("other-owner", job.id))
        database_bytes = self.layout.database_path().read_bytes()
        self.assertNotIn(job.dataset_sha256.encode("ascii"), database_bytes)
        self.assertNotIn(job.source_record_digest.encode("ascii"), database_bytes)
        self.assertNotIn(b"dataset_path", database_bytes)

    def test_list_and_delete_for_persona_are_owner_scoped(self) -> None:
        first = self._job("job-1")
        second = self._job("job-2")
        first = self.repository.save(self.owner_id, first)
        second = self.repository.save(self.owner_id, second)

        self.assertEqual([first, second], self.repository.list(self.owner_id, self.persona.id))
        self.assertEqual(2, self.repository.delete_for_persona(self.owner_id, self.persona.id))
        self.assertEqual([], self.repository.list(self.owner_id, self.persona.id))

    def test_stale_state_cannot_overwrite_newer_update_or_resurrect_a_deleted_job(self) -> None:
        original = self.repository.save(self.owner_id, self._job())
        submitted = self.repository.save(
            self.owner_id,
            original.start_provider_submission("2026-08-11T10:00:30+00:00"),
        )
        running = self.repository.save(
            self.owner_id,
            submitted.mark_running("provider-job-1", "2026-08-11T10:01:00+00:00"),
        )

        with self.assertRaises(TrainingJobRepositoryError) as stale:
            self.repository.save(
                self.owner_id,
                original.cancel("2026-08-11T10:01:00+00:00"),
            )
        self.assertEqual("training_job_conflict", stale.exception.code)
        self.assertEqual(running, self.repository.get(self.owner_id, original.id))

        self.assertTrue(self.repository.delete(self.owner_id, original.id))
        with self.assertRaises(TrainingJobRepositoryError) as deleted:
            self.repository.save(self.owner_id, running)
        self.assertEqual("training_job_conflict", deleted.exception.code)


if __name__ == "__main__":
    unittest.main()
