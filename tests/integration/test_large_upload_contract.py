import base64
import hashlib
import io
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.import_service import DEFAULT_MAX_IMPORT_BYTES, ImportService, ImportState
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR, EnvironmentMasterKeyProvider
from src.services.persona_repository import PersonaRepository
from src.services.persona_service import PersonaService
from src.services.storage import StorageLayout
from src.services.upload_service import UploadService


class LargeUploadContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_three_gib_job_does_not_preallocate_the_payload(self) -> None:
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"l" * MASTER_KEY_BYTES).decode("ascii")
        encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        personas = PersonaService(PersonaRepository(layout.database_path(), encryption))
        imports = ImportService(layout, personas)
        uploads = UploadService(layout, imports, encryption)
        persona = personas.create("妈妈", "mother")

        job = imports.create(
            persona.id,
            "wechat-export.zip",
            DEFAULT_MAX_IMPORT_BYTES,
            "application/zip",
        )
        one_byte = b"x"
        uploads.put_chunk(
            job.id,
            0,
            len(one_byte),
            hashlib.sha256(one_byte).hexdigest(),
            io.BytesIO(one_byte),
        )

        stored = imports.get(job.id)
        allocated = sum(path.stat().st_size for path in self.root.rglob("*") if path.is_file())
        self.assertEqual(ImportState.UPLOADING, stored.state)
        self.assertEqual(1, stored.received_bytes)
        self.assertLess(allocated, 1024 * 1024)
        self.assertFalse(uploads.payload_path(job.id).exists())


if __name__ == "__main__":
    unittest.main()
