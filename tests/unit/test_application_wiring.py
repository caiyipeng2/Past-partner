import base64
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ConfigurationError, ServerConfig
from src.services.blob_store import LocalBlobStore
from src.services.master_key import (
    EnvironmentMasterKeyProvider,
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
)
from src.services.storage import StorageLayout


class ApplicationStorageWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.environment = patch.dict(
            os.environ,
            {MASTER_KEY_ENV_VAR: base64.b64encode(b"w" * MASTER_KEY_BYTES).decode("ascii")},
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def config(self, backend: str | None = None) -> ServerConfig:
        return ServerConfig(
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="test",
            **({"storage_backend": backend} if backend is not None else {}),
        )

    def test_default_backend_wires_local_blob_store_at_configured_root(self) -> None:
        with patch("src.server.application.build_blob_store") as factory:
            factory.return_value = LocalBlobStore(StorageLayout(self.root))
            application = Application.from_config(self.config())

        factory.assert_called_once()
        self.assertEqual("local", factory.call_args.args[0])
        self.assertEqual(self.root.resolve(), factory.call_args.args[1].root)

        self.assertIsInstance(application.uploads.blob_store, LocalBlobStore)
        self.assertEqual(self.root.resolve(), application.uploads.blob_store.layout.root)

    def test_explicit_local_backend_wires_the_same_local_adapter(self) -> None:
        application = Application.from_config(self.config("local"))

        self.assertIsInstance(application.uploads.blob_store, LocalBlobStore)
        self.assertEqual(self.root.resolve(), application.uploads.blob_store.layout.root)

    def test_s3_backend_passes_validated_settings_to_blob_store_factory(self) -> None:
        config = ServerConfig(
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="test",
            storage_backend="s3",
            storage_s3_endpoint="http://127.0.0.1:9000",
            storage_s3_bucket="past-partner-test",
            storage_s3_region="local",
            storage_s3_access_key="access-key",
            storage_s3_secret_key="secret-key",
        )
        with patch("src.server.application.build_blob_store") as factory:
            factory.return_value = LocalBlobStore(StorageLayout(self.root))
            application = Application.from_config(config)

        factory.assert_called_once()
        self.assertEqual("s3", factory.call_args.args[0])
        settings = factory.call_args.kwargs["s3_settings"]
        self.assertEqual("past-partner-test", settings.bucket)
        self.assertEqual("http://127.0.0.1:9000", settings.endpoint)
        self.assertEqual("local", settings.region)
        self.assertIsNotNone(application.uploads.blob_store)

    def test_kms_source_passes_validated_settings_to_master_key_factory(self) -> None:
        config = ServerConfig(
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="test",
            master_key_source="kms",
            master_key_kms_key_id="alias/past-partner",
            master_key_kms_region="local",
            master_key_kms_endpoint="http://127.0.0.1:4566",
            master_key_kms_auto_provision=True,
        )
        with patch("src.server.application.build_master_key_provider") as master_factory:
            master_factory.return_value = EnvironmentMasterKeyProvider(
                {MASTER_KEY_ENV_VAR: base64.b64encode(b"w" * MASTER_KEY_BYTES).decode("ascii")}
            )
            Application.from_config(config)

        master_factory.assert_called_once()
        self.assertEqual(self.root, master_factory.call_args.args[0])
        self.assertEqual("kms", master_factory.call_args.kwargs["master_key_source"])
        self.assertEqual("alias/past-partner", master_factory.call_args.kwargs["kms_key_id"])
        self.assertEqual("local", master_factory.call_args.kwargs["kms_region"])
        self.assertEqual("http://127.0.0.1:4566", master_factory.call_args.kwargs["kms_endpoint"])
        self.assertTrue(master_factory.call_args.kwargs["kms_auto_provision"])

    def test_unsupported_backend_fails_before_application_writes_objects(self) -> None:
        with self.assertRaises(ConfigurationError) as captured:
            Application.from_config(self.config("azure"))

        self.assertEqual("storage_backend_unsupported", captured.exception.code)
        self.assertFalse((self.root / "payloads").exists())
        self.assertFalse((self.root / "upload-parts").exists())

    def test_application_wires_one_task_queue_to_the_shared_metadata_store(self) -> None:
        application = Application.from_config(self.config())

        self.assertIs(application.task_queue.metadata_store, application.metadata_store)


if __name__ == "__main__":
    unittest.main()
