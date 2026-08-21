import base64
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.server.application import Application
from src.server.config import ServerConfig
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR


class ApplicationReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(dir=Path.cwd()))
        self.environment = patch.dict(
            os.environ,
            {MASTER_KEY_ENV_VAR: base64.b64encode(b"r" * MASTER_KEY_BYTES).decode("ascii")},
        )
        self.environment.start()
        config = ServerConfig(data_dir=self.root, web_dir=Path.cwd() / "web", mode="test")
        self.application = Application.from_config(config)

    def tearDown(self) -> None:
        self.application.close()
        self.environment.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_readiness_reports_metadata_store_ok(self) -> None:
        result = self.application.readiness()

        self.assertEqual("ready", result["status"])
        self.assertEqual("ok", result["checks"]["metadata_store"])
        self.assertNotIn(str(self.root), str(result))

    def test_readiness_redacts_metadata_failure(self) -> None:
        class FailingStore:
            backend_name = "sqlite"

            def connect(self):
                raise RuntimeError(f"secret database path: {self.root}")

            def close(self):
                return None

        failing = FailingStore()
        failing.root = self.root
        self.application.metadata_store = failing

        result = self.application.readiness()

        self.assertEqual("not_ready", result["status"])
        self.assertEqual("unavailable", result["checks"]["metadata_store"])
        self.assertNotIn(str(self.root), str(result))
        self.assertNotIn("secret database path", str(result))


if __name__ == "__main__":
    unittest.main()
