import base64
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR, EnvironmentMasterKeyProvider
from src.services.persona_repository import PersonaRepository
from src.services.persona_service import PersonaNotFoundError, PersonaService
from src.services.storage import StorageLayout


class PersonaServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        layout = StorageLayout(self.root)
        key = base64.b64encode(b"s" * MASTER_KEY_BYTES).decode("ascii")
        encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.service = PersonaService(PersonaRepository(layout.database_path(), encryption))
        self.encryption = encryption

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_persists_and_reloads_persona(self) -> None:
        created = self.service.create("妈妈", "mother")
        reloaded_service = PersonaService(
            PersonaRepository(StorageLayout(self.root).database_path(), self.encryption)
        )

        loaded = reloaded_service.get(created.id)

        self.assertEqual(created, loaded)
        self.assertFalse((self.root / "personas").exists())

    def test_missing_persona_has_a_domain_error(self) -> None:
        with self.assertRaises(PersonaNotFoundError):
            self.service.get("62fe0eef-7053-4df5-87bb-7842e6c738c4")


if __name__ == "__main__":
    unittest.main()
