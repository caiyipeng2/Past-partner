"""P2-07 durable fine-tuning job state must fail closed on unverifiable results."""

from __future__ import annotations

import unittest

from src.domain.training_jobs import (
    TrainingJob,
    TrainingJobState,
    TrainingJobValidationError,
)


class TrainingJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pending = TrainingJob.pending(
            job_id="job-1",
            persona_id="persona-1",
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

    def test_completed_job_requires_verified_artifact_and_evaluation(self) -> None:
        started = self.pending.start_provider_submission("2026-08-11T10:00:30+00:00")
        running = started.mark_running("provider-job-1", "2026-08-11T10:01:00+00:00")

        with self.assertRaises(TrainingJobValidationError) as artifact_missing:
            running.complete("", {"status": "verified"}, "2026-08-11T10:02:00+00:00")
        self.assertEqual("training_result_unverified", artifact_missing.exception.code)

        with self.assertRaises(TrainingJobValidationError) as evaluation_missing:
            running.complete("artifact-1", {}, "2026-08-11T10:02:00+00:00")
        self.assertEqual("training_result_unverified", evaluation_missing.exception.code)

        completed = running.complete(
            "artifact-1",
            {"status": "verified", "score": 0.91},
            "2026-08-11T10:02:00+00:00",
        )

        self.assertEqual(TrainingJobState.COMPLETED, completed.state)
        self.assertEqual(100, completed.progress_percent)
        self.assertEqual("artifact-1", completed.artifact_id)
        self.assertEqual("verified", completed.evaluation["status"])

    def test_closed_job_rejects_further_transitions(self) -> None:
        cancelled = self.pending.cancel("2026-08-11T10:01:00+00:00")

        with self.assertRaises(TrainingJobValidationError) as captured:
            cancelled.mark_running("provider-job-1", "2026-08-11T10:02:00+00:00")

        self.assertEqual("training_job_closed", captured.exception.code)

    def test_round_trip_preserves_only_redacted_metadata(self) -> None:
        started = self.pending.start_provider_submission("2026-08-11T10:00:30+00:00")
        running = started.mark_running("provider-job-1", "2026-08-11T10:01:00+00:00")
        failed = running.fail(
            "provider_unavailable",
            retryable=True,
            updated_at="2026-08-11T10:02:00+00:00",
        )

        restored = TrainingJob.from_dict(failed.to_dict())

        self.assertEqual(failed, restored)
        self.assertEqual("provider_unavailable", restored.failure_code)
        self.assertTrue(restored.retryable)
        self.assertEqual("job-1", restored.diagnostic_id)
        self.assertNotIn("dataset_path", restored.to_dict())

    def test_submission_intent_and_local_cleanup_failure_are_explicit_metadata(self) -> None:
        started = self.pending.start_provider_submission("2026-08-11T10:00:30+00:00")
        recorded = started.record_cleanup_failure(
            "training_dataset_cleanup_failed",
            "2026-08-11T10:01:00+00:00",
        )

        self.assertTrue(recorded.submission_started)
        self.assertEqual("training_dataset_cleanup_failed", recorded.local_cleanup_failure_code)
        self.assertEqual(recorded, TrainingJob.from_dict(recorded.to_dict()))


if __name__ == "__main__":
    unittest.main()
