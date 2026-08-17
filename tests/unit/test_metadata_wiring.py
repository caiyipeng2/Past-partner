from __future__ import annotations

import base64
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ConfigurationError, ServerConfig
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR


class MetadataWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.environment = patch.dict(
            os.environ,
            {MASTER_KEY_ENV_VAR: base64.b64encode(b"m" * MASTER_KEY_BYTES).decode("ascii")},
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def config(self, backend: str = "sqlite") -> ServerConfig:
        return ServerConfig(
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="test",
            metadata_backend=backend,
        )

    def test_application_injects_one_shared_metadata_store(self) -> None:
        application = Application.from_config(self.config())

        stores = (
            application.auth.metadata_store,
            application.personas.repository.metadata_store,
            application.imports.repository.metadata_store,
            application.consents.repository.metadata_store,
            application.training.repository.metadata_store,
            application.conversations.repository.metadata_store,
        )
        self.assertEqual(1, len({id(store) for store in stores}))
        self.assertEqual("sqlite", stores[0].backend_name)

    def test_unknown_metadata_backend_fails_before_database_creation(self) -> None:
        with self.assertRaises(ConfigurationError) as captured:
            Application.from_config(self.config("postgres"))

        self.assertEqual("metadata_backend_unsupported", captured.exception.code)
        self.assertFalse(self.root.exists())

    def test_blank_metadata_backend_is_not_silently_defaulted(self) -> None:
        with self.assertRaises(ConfigurationError) as captured:
            Application.from_config(self.config(""))

        self.assertEqual("metadata_backend_unsupported", captured.exception.code)

    def test_metadata_backend_is_loaded_from_environment(self) -> None:
        with patch.dict(os.environ, {"PAST_PARTNER_METADATA_BACKEND": "postgres"}):
            with self.assertRaises(ConfigurationError) as captured:
                ServerConfig.from_env()

        self.assertEqual("metadata_backend_unsupported", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
