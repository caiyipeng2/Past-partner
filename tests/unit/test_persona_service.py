import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.services.persona_service import PersonaNotFoundError, PersonaService
from src.services.storage import StorageLayout


class PersonaServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.service = PersonaService(StorageLayout(self.root))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_persists_and_reloads_persona(self) -> None:
        created = self.service.create("妈妈", "mother")
        reloaded_service = PersonaService(StorageLayout(self.root))

        loaded = reloaded_service.get(created.id)

        self.assertEqual(created, loaded)
        stored_files = list((self.root / "personas").iterdir())
        self.assertEqual([f"{created.id}.json"], [item.name for item in stored_files])

    def test_missing_persona_has_a_domain_error(self) -> None:
        with self.assertRaises(PersonaNotFoundError):
            self.service.get("62fe0eef-7053-4df5-87bb-7842e6c738c4")


if __name__ == "__main__":
    unittest.main()
