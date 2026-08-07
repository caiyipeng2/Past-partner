import base64
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR, EnvironmentMasterKeyProvider
from src.services.local_auth import LocalAuthService
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
        self.auth = LocalAuthService(layout.database_path(), encryption, mode="test")

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

    def test_persists_extended_persona_fields(self) -> None:
        created = self.service.create(
            "小雨",
            "friend",
            preferred_address="你",
            user_address="小雨",
            relationship_description="大学同学",
            tone_boundaries=("温和",),
            forbidden_topics=("隐私",),
        )

        loaded = self.service.get(created.id)

        self.assertEqual("你", loaded.preferred_address)
        self.assertEqual("小雨", loaded.user_address)
        self.assertEqual("大学同学", loaded.relationship_description)
        self.assertEqual(("温和",), loaded.tone_boundaries)
        self.assertEqual(("隐私",), loaded.forbidden_topics)

    def test_updates_and_persists_a_persona(self) -> None:
        created = self.service.create("小雨", "friend", preferred_address="你")

        updated = self.service.update(
            created.id,
            {
                "display_name": "小雨同学",
                "preferred_address": None,
                "relationship_description": "大学同学",
            },
        )

        loaded = self.service.get(created.id)
        self.assertEqual("小雨同学", updated.display_name)
        self.assertIsNone(loaded.preferred_address)
        self.assertEqual("大学同学", loaded.relationship_description)
        self.assertEqual(created.id, loaded.id)

    def test_update_is_scoped_to_the_requested_owner(self) -> None:
        created = self.service.create(self.auth.owner_id, "小雨", "friend")

        with self.assertRaises(PersonaNotFoundError):
            self.service.update("owner-b", created.id, {"display_name": "越权"})

    def test_delete_is_scoped_to_the_requested_owner(self) -> None:
        created = self.service.create(self.auth.owner_id, "小雨", "friend")

        with self.assertRaises(PersonaNotFoundError):
            self.service.delete("owner-b", created.id)

        self.assertEqual(created, self.service.get(self.auth.owner_id, created.id))

    def test_missing_persona_has_a_domain_error(self) -> None:
        with self.assertRaises(PersonaNotFoundError):
            self.service.get("62fe0eef-7053-4df5-87bb-7842e6c738c4")


if __name__ == "__main__":
    unittest.main()
