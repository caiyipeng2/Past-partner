"""R1-04 redacted worker observation and alert contracts."""

from __future__ import annotations

import unittest
import base64
import shutil
from pathlib import Path
from uuid import uuid4

from src.domain.worker_observability import (
    WorkerAlert,
    WorkerAlertSeverity,
    WorkerObservation,
    WorkerObservationOutcome,
    WorkerObservationValidationError,
)
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.local_auth import LocalAuthService
from src.services.master_key import EnvironmentMasterKeyProvider, MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.storage import StorageLayout
from src.services.worker_observability import WorkerObservability


class WorkerObservationDomainTests(unittest.TestCase):
    def test_accepts_bounded_redacted_lifecycle_value(self) -> None:
        observation = WorkerObservation(
            worker_id="worker-a",
            task_type="training.submit",
            outcome=WorkerObservationOutcome.RETRYABLE_FAILURE,
            observed_at="2026-08-25T10:00:00+00:00",
            duration_ms=125,
            failure_code="provider_timeout",
        )

        self.assertEqual("worker-a", observation.worker_id)
        self.assertEqual("training.submit", observation.task_type)
        self.assertEqual(WorkerObservationOutcome.RETRYABLE_FAILURE, observation.outcome)
        self.assertEqual(
            {
                "worker_id": "worker-a",
                "task_type": "training.submit",
                "outcome": "retryable_failure",
                "observed_at": "2026-08-25T10:00:00+00:00",
                "duration_ms": 125,
                "failure_code": "provider_timeout",
            },
            observation.to_mapping(),
        )

    def test_idle_observation_uses_fixed_internal_task_type_without_failure_code(self) -> None:
        observation = WorkerObservation(
            worker_id="worker-a",
            task_type="worker.idle",
            outcome=WorkerObservationOutcome.IDLE,
            observed_at="2026-08-25T10:00:00+00:00",
            duration_ms=0,
        )

        self.assertIsNone(observation.failure_code)
        self.assertEqual("worker.idle", observation.to_mapping()["task_type"])

    def test_rejects_unbounded_or_secret_bearing_values(self) -> None:
        invalid = (
            {"worker_id": "worker with spaces"},
            {"worker_id": "worker-a", "task_type": "worker.idle", "duration_ms": -1},
            {"worker_id": "worker-a", "task_type": "worker.idle", "duration_ms": 3_600_001},
            {
                "worker_id": "worker-a",
                "task_type": "worker.idle",
                "outcome": "unexpected",
            },
            {
                "worker_id": "worker-a",
                "task_type": "worker.idle",
                "outcome": WorkerObservationOutcome.IDLE,
                "failure_code": "provider-key=super-secret",
            },
            {
                "worker_id": "worker-a",
                "task_type": "worker.idle",
                "outcome": WorkerObservationOutcome.IDLE,
                "observed_at": "not-a-timestamp",
            },
        )

        for overrides in invalid:
            values = {
                "worker_id": "worker-a",
                "task_type": "worker.idle",
                "outcome": WorkerObservationOutcome.IDLE,
                "observed_at": "2026-08-25T10:00:00+00:00",
                "duration_ms": 0,
            }
            values.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaises(WorkerObservationValidationError):
                    WorkerObservation(**values)

    def test_failure_outcomes_require_stable_failure_code(self) -> None:
        for outcome in (
            WorkerObservationOutcome.RETRYABLE_FAILURE,
            WorkerObservationOutcome.TERMINAL_FAILURE,
            WorkerObservationOutcome.LEASE_LOST,
        ):
            with self.subTest(outcome=outcome):
                with self.assertRaises(WorkerObservationValidationError):
                    WorkerObservation(
                        worker_id="worker-a",
                        task_type="training.submit",
                        outcome=outcome,
                        observed_at="2026-08-25T10:00:00+00:00",
                        duration_ms=1,
                    )


class WorkerAlertDomainTests(unittest.TestCase):
    def test_alert_contains_only_bounded_operational_counts(self) -> None:
        alert = WorkerAlert(
            code="worker_failure_rate_high",
            severity=WorkerAlertSeverity.WARNING,
            worker_id="worker-a",
            task_type="training.submit",
            window_start="2026-08-25T09:55:00+00:00",
            observed_at="2026-08-25T10:00:00+00:00",
            sample_count=4,
            failure_count=3,
        )

        self.assertEqual(
            {
                "code": "worker_failure_rate_high",
                "severity": "warning",
                "worker_id": "worker-a",
                "task_type": "training.submit",
                "window_start": "2026-08-25T09:55:00+00:00",
                "observed_at": "2026-08-25T10:00:00+00:00",
                "sample_count": 4,
                "failure_count": 3,
            },
            alert.to_mapping(),
        )
        self.assertNotIn("owner", repr(alert).lower())
        self.assertNotIn("payload", repr(alert).lower())

    def test_alert_rejects_invalid_counts_and_unbounded_codes(self) -> None:
        with self.assertRaises(WorkerObservationValidationError):
            WorkerAlert(
                code="secret=provider-key",
                severity=WorkerAlertSeverity.WARNING,
                worker_id="worker-a",
                task_type="training.submit",
                window_start="2026-08-25T09:55:00+00:00",
                observed_at="2026-08-25T10:00:00+00:00",
                sample_count=1,
                failure_count=2,
            )


class WorkerObservabilityServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"o" * MASTER_KEY_BYTES).decode("ascii")
        encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        auth = LocalAuthService(layout.database_path(), encryption, mode="test")
        self.store = auth.metadata_store
        self.observability = WorkerObservability(
            self.store,
            retention_seconds=60,
            max_observations_per_worker=10,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_persists_redacted_rows_and_prunes_by_age_and_worker_cap(self) -> None:
        self.observability = WorkerObservability(
            self.store,
            retention_seconds=60,
            max_observations_per_worker=2,
        )
        self.observability.record(
            WorkerObservation(
                worker_id="worker-a",
                task_type="training.submit",
                outcome=WorkerObservationOutcome.SUCCEEDED,
                observed_at="2026-08-25T10:00:00+00:00",
                duration_ms=4,
            ),
            now="2026-08-25T10:00:00+00:00",
        )
        for second in (20, 21, 22):
            self.observability.record(
                WorkerObservation(
                    worker_id="worker-a",
                    task_type="training.submit",
                    outcome=WorkerObservationOutcome.SUCCEEDED,
                    observed_at=f"2026-08-25T10:00:{second:02d}+00:00",
                    duration_ms=4,
                ),
                now=f"2026-08-25T10:00:{second:02d}+00:00",
            )

        rows = self.observability.recent(worker_id="worker-a")
        self.assertEqual(2, len(rows))
        self.assertEqual(
            ["2026-08-25T10:00:22+00:00", "2026-08-25T10:00:21+00:00"],
            [row.observed_at for row in rows],
        )
        with self.store.transaction() as connection:
            raw = connection.execute(
                "SELECT worker_id, task_type, outcome, failure_code FROM worker_observations"
            ).fetchall()
        self.assertEqual(2, len(raw))
        self.assertNotIn("payload", repr(raw).lower())
        self.assertNotIn("provider-key", repr(raw).lower())

    def test_normalizes_observation_timestamps_to_utc_before_cursor_queries(self) -> None:
        self.observability.record(
            WorkerObservation(
                worker_id="worker-a",
                task_type="training.submit",
                outcome=WorkerObservationOutcome.SUCCEEDED,
                observed_at="2026-08-25T18:00:00+08:00",
                duration_ms=4,
            ),
            now="2026-08-25T10:00:00+00:00",
        )

        rows = self.observability.recent(since="2026-08-25T09:59:00+00:00")
        self.assertEqual(("2026-08-25T10:00:00+00:00",), tuple(row.observed_at for row in rows))

    def test_evaluates_failure_rate_and_stale_worker_alerts_deterministically(self) -> None:
        for index, outcome in enumerate(
            (
                WorkerObservationOutcome.TERMINAL_FAILURE,
                WorkerObservationOutcome.RETRYABLE_FAILURE,
                WorkerObservationOutcome.SUCCEEDED,
            )
        ):
            self.observability.record(
                WorkerObservation(
                    worker_id="worker-a",
                    task_type="training.submit",
                    outcome=outcome,
                    observed_at=f"2026-08-25T09:59:0{index}+00:00",
                    duration_ms=4,
                    failure_code=None if outcome is WorkerObservationOutcome.SUCCEEDED else "task_failed",
                ),
                now="2026-08-25T10:00:00+00:00",
            )

        alerts = self.observability.evaluate_alerts(
            worker_ids=("worker-a", "worker-b"),
            now="2026-08-25T10:00:00+00:00",
            window_seconds=120,
            heartbeat_timeout_seconds=120,
            min_samples=3,
            failure_rate=0.5,
        )

        self.assertEqual(
            ["worker_failure_rate_high", "worker_no_heartbeat"],
            [alert.code for alert in alerts],
        )
        self.assertEqual("worker-a", alerts[0].worker_id)
        self.assertEqual(2, alerts[0].failure_count)
        self.assertEqual(3, alerts[0].sample_count)
        self.assertEqual("worker-b", alerts[1].worker_id)
        self.assertEqual(0, alerts[1].sample_count)

    def test_rejects_invalid_alert_parameters(self) -> None:
        with self.assertRaises(ValueError):
            self.observability.evaluate_alerts(window_seconds=0)
        with self.assertRaises(ValueError):
            self.observability.evaluate_alerts(failure_rate=1.1)


if __name__ == "__main__":
    unittest.main()
