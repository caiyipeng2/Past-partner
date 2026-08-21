from __future__ import annotations

import base64
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.learning.long_term_memory import LongTermMemoryExtractor
from src.learning.style_profile import StyleProfileExtractor
from src.domain.messages import NormalizedMessage
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.learning_repository import LearningRepository
from src.services.local_auth import LocalAuthService
from src.services.master_key import EnvironmentMasterKeyProvider, MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.persona_repository import PersonaRepository
from src.services.persona_service import PersonaService
from src.services.storage import StorageLayout


def _message(record_id: str, sender_id: str, content: str) -> NormalizedMessage:
    return NormalizedMessage.from_mapping(
        {
            "record_id": record_id,
            "sender_id": sender_id,
            "sender_name": sender_id,
            "content": content,
            "timestamp": "2026-08-21T10:00:00+00:00",
            "message_type": "text",
        }
    )


class LearningRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"l" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.auth = LocalAuthService(self.layout.database_path(), self.encryption, mode="test")
        self.owner_id = self.auth.owner_id
        self.personas = PersonaService(PersonaRepository(self.layout.database_path(), self.encryption))
        self.persona = self.personas.create(self.owner_id, "学习对象", "friend")
        self.repository = LearningRepository(self.layout.database_path(), self.encryption)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _profile(self):
        return StyleProfileExtractor().extract(
            (
                _message("1" * 64, "persona", "开心呀！"),
                _message("2" * 64, "user", "今天怎么样？"),
            ),
            persona_sender_ids={"persona"},
            user_sender_ids={"user"},
            relationship_type="friend",
            preferred_address="小伙伴",
        )

    def _memory(self):
        return LongTermMemoryExtractor().extract(
            (_message("a" * 64, "persona", "我们周末一起去看电影吧。"),),
            persona_sender_ids={"persona"},
            relationship_type="friend",
        )

    def test_profile_and_memory_survive_a_new_repository_instance(self) -> None:
        profile = self.repository.save_style_profile(self.owner_id, self.persona.id, self._profile())
        memory = self.repository.save_memory(self.owner_id, self.persona.id, self._memory())

        reopened = LearningRepository(self.layout.database_path(), self.encryption)

        self.assertEqual(profile.to_dict(), reopened.get_style_profile(self.owner_id, self.persona.id).to_dict())
        self.assertEqual(memory.to_dict(), reopened.get_memory(self.owner_id, self.persona.id).to_dict())

    def test_memory_review_and_retrieval_use_persisted_index(self) -> None:
        memory = self.repository.save_memory(self.owner_id, self.persona.id, self._memory())
        candidate_id = memory.candidates[0].memory_id
        self.repository.review_memory(self.owner_id, self.persona.id, candidate_id, "accepted")

        result = LearningRepository(self.layout.database_path(), self.encryption).retrieve(
            self.owner_id,
            self.persona.id,
            "周末电影",
        )

        self.assertEqual([candidate_id], [item.memory_id for item in result.memories])
        self.assertEqual("accepted", result.memories[0].review_state)

    def test_owner_scope_and_delete_for_persona_are_enforced(self) -> None:
        self.repository.save_style_profile(self.owner_id, self.persona.id, self._profile())
        self.repository.save_memory(self.owner_id, self.persona.id, self._memory())

        self.assertIsNone(self.repository.get_style_profile("other-owner", self.persona.id))
        self.assertIsNone(self.repository.get_memory("other-owner", self.persona.id))
        self.assertEqual(
            {"style_profiles": 1, "long_term_memories": 1, "vector_indexes": 1},
            self.repository.delete_for_persona(self.owner_id, self.persona.id),
        )
        self.assertIsNone(self.repository.get_style_profile(self.owner_id, self.persona.id))

    def test_learning_payload_is_encrypted_at_rest(self) -> None:
        self.repository.save_style_profile(self.owner_id, self.persona.id, self._profile())
        self.repository.save_memory(self.owner_id, self.persona.id, self._memory())
        database_bytes = self.layout.database_path().read_bytes()

        self.assertNotIn("小伙伴".encode("utf-8"), database_bytes)
        self.assertNotIn("周末一起去看电影".encode("utf-8"), database_bytes)


if __name__ == "__main__":
    unittest.main()
