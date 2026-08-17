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
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
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

    def test_unsupported_backend_fails_before_application_writes_objects(self) -> None:
        with self.assertRaises(ConfigurationError) as captured:
            Application.from_config(self.config("s3"))

        self.assertEqual("storage_backend_unsupported", captured.exception.code)
        self.assertFalse((self.root / "payloads").exists())
        self.assertFalse((self.root / "upload-parts").exists())


if __name__ == "__main__":
    unittest.main()
