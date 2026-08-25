"""R1-04 external worker process launch contracts."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.server.application import Application
from src.server.config import ServerConfig
from src.domain.task_queue import utc_now
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR


class ExternalWorkerLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="past-partner-worker-"))
        self.key = base64.b64encode(b"r" * MASTER_KEY_BYTES).decode("ascii")
        self.environment = {
            MASTER_KEY_ENV_VAR: self.key,
            "PAST_PARTNER_MODE": "test",
            "PAST_PARTNER_DATA_DIR": str(self.root),
            "PAST_PARTNER_WEB_DIR": str(Path.cwd() / "web"),
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_worker(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment.update(self.environment)
        return subprocess.run(
            [sys.executable, "-m", "src.worker", *arguments],
            cwd=Path.cwd(),
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def test_once_process_claims_probe_and_persists_redacted_result(self) -> None:
        with patch.dict(os.environ, self.environment):
            application = Application.from_config(
                ServerConfig(data_dir=self.root, web_dir=Path.cwd() / "web", mode="test")
            )
        owner_id = application.auth.owner_id
        task = application.task_queue.enqueue(
            owner_id,
            "worker.probe",
            {"secret": "do-not-persist", "value": 1},
            now=utc_now(),
        )
        application.close()

        result = self._run_worker("--once", "--worker-id", "integration-worker")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("do-not-persist", result.stdout + result.stderr)
        self.assertNotIn("Serving on", result.stdout + result.stderr)
        with patch.dict(os.environ, self.environment):
            application = Application.from_config(
                ServerConfig(data_dir=self.root, web_dir=Path.cwd() / "web", mode="test")
            )
        try:
            completed = application.task_queue.get(owner_id, task.id)
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual("succeeded", completed.state.value)
            self.assertEqual(
                {"ok": True, "payload_keys": ["secret", "value"]},
                completed.result,
            )
            self.assertNotIn("do-not-persist", repr(completed.result))
        finally:
            application.close()

    def test_idle_once_process_exits_without_http_server(self) -> None:
        result = self._run_worker("--once", "--worker-id", "idle-worker")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("Serving on", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
