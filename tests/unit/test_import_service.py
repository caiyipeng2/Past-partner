import base64
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.import_repository import ImportRepository
from src.services.import_service import (
    DEFAULT_MAX_IMPORT_BYTES,
    ImportFile,
    ImportNotFoundError,
    ImportService,
    ImportState,
    ImportValidationError,
)
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR, EnvironmentMasterKeyProvider
from src.services.persona_repository import PersonaRepository
from src.services.persona_service import PersonaService
from src.services.storage import StorageLayout


class ImportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"i" * MASTER_KEY_BYTES).decode("ascii")
        encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.personas = PersonaService(PersonaRepository(layout.database_path(), encryption))
        repository = ImportRepository(layout.database_path(), encryption)
        self.imports = ImportService(repository, self.personas)
        self.persona = self.personas.create("小雨", "friend")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_requires_an_existing_persona(self) -> None:
        with self.assertRaises(ImportValidationError) as captured:
            self.imports.create(
                persona_id="a03e9461-5be7-45bf-83eb-dad57620dc03",
                source_name="history.zip",
                total_bytes=20,
                media_type="application/zip",
            )
        self.assertEqual("persona_not_found", captured.exception.code)

    def test_accepts_default_three_gib_boundary(self) -> None:
        job = self.imports.create(
            persona_id=self.persona.id,
            source_name="聊天记录.zip",
            total_bytes=DEFAULT_MAX_IMPORT_BYTES,
            media_type="application/zip",
        )

        self.assertEqual(3 * 1024**3, DEFAULT_MAX_IMPORT_BYTES)
        self.assertEqual(ImportState.CREATED, job.state)
        self.assertEqual(DEFAULT_MAX_IMPORT_BYTES, job.total_bytes)

    def test_creates_an_ordered_multi_file_manifest_with_independent_ids(self) -> None:
        job = self.imports.create(
            persona_id=self.persona.id,
            files=[
                {
                    "source_name": "wechat.txt",
                    "media_type": "text/plain",
                    "total_bytes": 5,
                    "sha256": "a" * 64,
                },
                {
                    "source_name": "photo.jpg",
                    "media_type": "image/jpeg",
                    "total_bytes": 7,
                },
            ],
        )

        self.assertEqual(12, job.total_bytes)
        self.assertEqual(2, len(job.files))
        self.assertNotEqual(job.files[0].file_id, job.files[1].file_id)
        self.assertEqual(["wechat.txt", "photo.jpg"], [item.source_name for item in job.files])
        self.assertEqual(job, type(job).from_dict(job.to_dict()))

    def test_rejects_duplicate_or_incomplete_multi_file_metadata(self) -> None:
        with self.assertRaises(ImportValidationError) as duplicate:
            self.imports.create(
                persona_id=self.persona.id,
                files=[
                    {"file_id": "same", "source_name": "a.txt", "media_type": "text/plain", "total_bytes": 1},
                    {"file_id": "same", "source_name": "b.txt", "media_type": "text/plain", "total_bytes": 1},
                ],
            )
        self.assertEqual("duplicate_file_id", duplicate.exception.code)

        with self.assertRaises(ImportValidationError) as mismatch:
            self.imports.create(
                persona_id=self.persona.id,
                total_bytes=99,
                files=[{"source_name": "a.txt", "media_type": "text/plain", "total_bytes": 1}],
            )
        self.assertEqual("manifest_total_mismatch", mismatch.exception.code)

    def test_rejects_size_over_configured_limit(self) -> None:
        with self.assertRaises(ImportValidationError) as captured:
            self.imports.create(
                persona_id=self.persona.id,
                source_name="history.zip",
                total_bytes=DEFAULT_MAX_IMPORT_BYTES + 1,
                media_type="application/zip",
            )
        self.assertEqual("import_too_large", captured.exception.code)

    def test_source_name_never_controls_manifest_path(self) -> None:
        job = self.imports.create(
            persona_id=self.persona.id,
            source_name="../../family-chat.zip",
            total_bytes=0,
            media_type="application/zip",
        )

        self.assertFalse((self.root / "imports").exists())
        self.assertEqual("../../family-chat.zip", self.imports.get(job.id).source_name)
        self.assertNotIn("family-chat.zip".encode("utf-8"), (self.root / "database" / "past-partner.sqlite3").read_bytes())

    def test_missing_import_has_a_domain_error(self) -> None:
        with self.assertRaises(ImportNotFoundError):
            self.imports.get("e2bbe66f-e275-4c43-8f8b-e7eb1c910da7")


if __name__ == "__main__":
    unittest.main()
