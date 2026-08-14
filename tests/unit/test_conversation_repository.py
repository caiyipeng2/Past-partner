import base64
import shutil
import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.conversations import Conversation
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.conversation_repository import ConversationRepository
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR, EnvironmentMasterKeyProvider
from src.services.storage import StorageLayout


class ConversationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        key = base64.b64encode(b"c" * MASTER_KEY_BYTES).decode("ascii")
        encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.layout = StorageLayout(self.root)
        self.repository = ConversationRepository(self.layout.database_path(), encryption)
        with sqlite3.connect(self.layout.database_path()) as connection:
            connection.execute(
                "INSERT INTO local_users (id, kind, record_version, encrypted_payload) VALUES (?, 'owner', 1, X'01')",
                ("owner-1",),
            )
            connection.execute(
                "INSERT INTO personas (id, owner_id, record_version, encrypted_payload) VALUES (?, ?, 1, X'02')",
                ("persona-1", "owner-1"),
            )
            connection.execute(
                "INSERT INTO personas (id, owner_id, record_version, encrypted_payload) VALUES (?, ?, 1, X'03')",
                ("persona-2", "owner-1"),
            )
            connection.commit()

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_conversation_and_messages_are_encrypted_and_round_trip(self) -> None:
        conversation = Conversation.create(
            persona_id="persona-1", provider_id="test", model_id="deterministic"
        )
        conversation = self.repository.save("owner-1", conversation)
        updated = self.repository.append_messages(
            "owner-1",
            conversation.add_user_and_assistant("你好", "测试回复：你好"),
        )
        loaded = self.repository.get("owner-1", conversation.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(updated, loaded)
        self.assertEqual(["user", "assistant"], [item.role for item in loaded.messages])
        database_bytes = self.layout.database_path().read_bytes()
        self.assertNotIn("你好".encode("utf-8"), database_bytes)
        with sqlite3.connect(self.layout.database_path()) as connection:
            row = connection.execute(
                "SELECT owner_id, persona_id, encrypted_payload FROM conversations"
            ).fetchone()
        self.assertEqual(("owner-1", "persona-1"), row[:2])
        self.assertIsInstance(row[2], bytes)

    def test_owner_and_persona_filters_are_enforced(self) -> None:
        first = self.repository.save(
            "owner-1", Conversation.create(persona_id="persona-1", provider_id="test", model_id="deterministic")
        )
        second = self.repository.save(
            "owner-1", Conversation.create(persona_id="persona-2", provider_id="test", model_id="deterministic")
        )
        self.assertEqual([first.id], [item.id for item in self.repository.list("owner-1", "persona-1")])
        self.assertEqual([], self.repository.list("owner-2"))
        self.assertEqual(1, self.repository.delete_for_persona("owner-1", "persona-1"))
        self.assertIsNone(self.repository.get("owner-1", first.id))
        self.assertIsNotNone(self.repository.get("owner-1", second.id))
