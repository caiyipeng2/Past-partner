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
from src.services.sqlite_metadata_store import SQLiteMetadataStore


class _PostgreSQLTestStore(SQLiteMetadataStore):
    backend_name = "postgresql"

    def __init__(self, database_path: Path) -> None:
        super().__init__(database_path)
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


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
            Application.from_config(self.config("mysql"))

        self.assertEqual("metadata_backend_unsupported", captured.exception.code)
        self.assertFalse(self.root.exists())

    def test_blank_metadata_backend_is_not_silently_defaulted(self) -> None:
        with self.assertRaises(ConfigurationError) as captured:
            Application.from_config(self.config(""))

        self.assertEqual("metadata_backend_unsupported", captured.exception.code)

    def test_metadata_backend_is_loaded_from_environment(self) -> None:
        with patch.dict(os.environ, {"PAST_PARTNER_METADATA_BACKEND": "mysql"}):
            with self.assertRaises(ConfigurationError) as captured:
                ServerConfig.from_env()

        self.assertEqual("metadata_backend_unsupported", captured.exception.code)

    def test_postgresql_backend_wires_one_store_and_closes_it(self) -> None:
        config = ServerConfig(
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="test",
            metadata_backend="postgres",
            metadata_dsn="postgresql://user:password@example.invalid/past_partner",
            metadata_pool_min_size=2,
            metadata_pool_max_size=6,
        )
        store = _PostgreSQLTestStore(self.root / "database" / "past-partner.sqlite3")
        with patch("src.server.application.build_metadata_store", return_value=store) as factory:
            application = Application.from_config(config)

        factory.assert_called_once_with(
            "postgresql",
            self.root.resolve() / "database" / "past-partner.sqlite3",
            dsn=config.metadata_dsn,
            pool_min_size=2,
            pool_max_size=6,
        )
        self.assertIs(store, application.metadata_store)
        application.close()
        application.close()
        self.assertEqual(1, store.close_count)


if __name__ == "__main__":
    unittest.main()
