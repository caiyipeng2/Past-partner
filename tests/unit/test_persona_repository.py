import base64
import shutil
import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.personas import Persona
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.master_key import (
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
    EnvironmentMasterKeyProvider,
)
from src.services.persona_repository import PersonaRepository, PersonaRepositoryError
from src.services.persona_service import PersonaService
from src.services.local_auth import LocalAuthService
from src.services.storage import StorageLayout


class PersonaRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"p" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.repository = PersonaRepository(self.layout.database_path(), self.encryption)
        self.auth = LocalAuthService(self.layout.database_path(), self.encryption, mode="test")
        self.service = PersonaService(self.repository)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_persona_metadata_is_encrypted_in_sqlite_and_round_trips(self) -> None:
        persona = self.service.create("小雨", "custom", "高中同学")

        self.assertFalse(self.layout.object_path("personas", persona.id, ".json").exists())
        self.assertEqual(persona, self.service.get(persona.id))
        self.assertEqual([persona], self.service.list())

        database_bytes = self.layout.database_path().read_bytes()
        self.assertNotIn("小雨".encode("utf-8"), database_bytes)
        self.assertNotIn("高中同学".encode("utf-8"), database_bytes)
        with sqlite3.connect(self.layout.database_path()) as connection:
            identifier, envelope = connection.execute(
                "SELECT id, encrypted_payload FROM personas"
            ).fetchone()
        self.assertEqual(persona.id, identifier)
        self.assertIsInstance(envelope, bytes)
        self.assertNotIn(persona.display_name.encode("utf-8"), envelope)

    def test_extended_persona_metadata_remains_encrypted(self) -> None:
        persona = self.service.create(
            "小雨",
            "friend",
            preferred_address="你",
            user_address="小雨",
            relationship_description="大学同学",
            tone_boundaries=("温和",),
            forbidden_topics=("家庭隐私",),
        )

        database_bytes = self.layout.database_path().read_bytes()
        self.assertNotIn("大学同学".encode("utf-8"), database_bytes)
        self.assertNotIn("家庭隐私".encode("utf-8"), database_bytes)
        self.assertEqual("家庭隐私", self.service.get(persona.id).forbidden_topics[0])

    def test_update_reencrypts_persona_metadata_without_plaintext_storage(self) -> None:
        persona = self.service.create("小雨", "friend", relationship_description="旧描述")
        updated = self.service.update(
            persona.id,
            {"relationship_description": "新描述", "forbidden_topics": ["家庭隐私"]},
        )

        self.assertEqual("新描述", updated.relationship_description)
        database_bytes = self.layout.database_path().read_bytes()
        self.assertNotIn("新描述".encode("utf-8"), database_bytes)
        self.assertNotIn("家庭隐私".encode("utf-8"), database_bytes)

    def test_tampered_persona_record_fails_closed(self) -> None:
        persona = self.service.create("小雨", "friend")
        with sqlite3.connect(self.layout.database_path()) as connection:
            envelope = bytearray(
                connection.execute(
                    "SELECT encrypted_payload FROM personas WHERE id = ?", (persona.id,)
                ).fetchone()[0]
            )
            envelope[-1] ^= 1
            connection.execute(
                "UPDATE personas SET encrypted_payload = ? WHERE id = ?",
                (bytes(envelope), persona.id),
            )
            connection.commit()

        with self.assertRaises(PersonaRepositoryError) as captured:
            self.service.get(persona.id)

        self.assertEqual("persona_record_authentication_failed", captured.exception.code)

    def test_record_cannot_be_rebound_to_a_different_persona_id(self) -> None:
        persona = self.service.create("小雨", "friend")
        rebound_id = str(uuid4())
        with sqlite3.connect(self.layout.database_path()) as connection:
            connection.execute(
                "UPDATE personas SET id = ? WHERE id = ?", (rebound_id, persona.id)
            )
            connection.commit()

        with self.assertRaises(PersonaRepositoryError) as captured:
            self.service.get(rebound_id)

        self.assertEqual("persona_record_authentication_failed", captured.exception.code)

    def test_missing_persona_is_reported_without_filesystem_fallback(self) -> None:
        legacy = Persona.create("旧记录", "friend")
        self.layout.write_json("personas", legacy.id, legacy.to_dict())

        with self.assertRaises(LookupError):
            self.service.get(legacy.id)
        self.assertEqual([], self.service.list())

    def test_migrates_legacy_json_before_removing_plaintext_source(self) -> None:
        legacy = Persona.create("待迁移", "relative")
        legacy_path = self.layout.write_json("personas", legacy.id, legacy.to_dict())

        migrated = self.repository.migrate_legacy_json(self.root / "personas")

        self.assertEqual(1, migrated)
        self.assertFalse(legacy_path.exists())
        self.assertEqual(legacy, self.service.get(legacy.id))
        self.assertNotIn("待迁移".encode("utf-8"), self.layout.database_path().read_bytes())

    def test_read_connections_are_closed_before_data_directory_cleanup(self) -> None:
        self.service.create("清理测试", "friend")

        self.service.list()
        shutil.rmtree(self.root)

        self.assertFalse(self.root.exists())

    def test_personas_are_scoped_to_the_owner_id(self) -> None:
        persona = self.service.create(self.auth.owner_id, "小雨", "friend")

        self.assertEqual([persona], self.service.list(self.auth.owner_id))
        self.assertEqual([], self.service.list("owner-b"))
        with self.assertRaises(LookupError):
            self.service.get("owner-b", persona.id)


if __name__ == "__main__":
    unittest.main()
