from __future__ import annotations

import base64
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.messages import NormalizedMessage
from src.learning.long_term_memory import LongTermMemoryExtractor
from src.learning.style_profile import StyleProfileExtractor
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.learning_repository import LearningRepository
from src.services.learning_service import LearningService, LearningServiceError
from src.services.local_auth import LocalAuthService
from src.services.master_key import EnvironmentMasterKeyProvider, MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.persona_repository import PersonaRepository
from src.services.persona_service import PersonaNotFoundError, PersonaService
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


class LearningServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"s" * MASTER_KEY_BYTES).decode("ascii")
        encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        auth = LocalAuthService(self.layout.database_path(), encryption, mode="test")
        self.owner_id = auth.owner_id
        self.personas = PersonaService(PersonaRepository(self.layout.database_path(), encryption))
        self.persona = self.personas.create(self.owner_id, "学习对象", "friend")
        self.repository = LearningRepository(self.layout.database_path(), encryption)
        self.service = LearningService(self.repository, self.personas)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _profile(self):
        return StyleProfileExtractor().extract(
            (_message("1" * 64, "persona", "开心呀！"),),
            persona_sender_ids={"persona"},
            relationship_type="friend",
        )

    def _memory(self):
        return LongTermMemoryExtractor().extract(
            (_message("a" * 64, "persona", "我们周末一起去看电影吧。"),),
            persona_sender_ids={"persona"},
            relationship_type="friend",
        )

    def test_persona_learning_survives_service_restart_and_review(self) -> None:
        self.service.save_style_profile(self.owner_id, self.persona.id, self._profile())
        memory = self.service.save_memory(self.owner_id, self.persona.id, self._memory())
        reviewed = self.service.review_memory(
            self.owner_id,
            self.persona.id,
            memory.candidates[0].memory_id,
            "accepted",
        )

        reopened = LearningService(self.repository, self.personas)
        result = reopened.retrieve(self.owner_id, self.persona.id, "周末电影")

        self.assertEqual("accepted", reviewed.candidates[0].review_state)
        self.assertEqual(1, len(result.memories))

    def test_unknown_persona_is_rejected_before_learning_lookup(self) -> None:
        with self.assertRaises(LearningServiceError) as captured:
            self.service.get_memory(self.owner_id, "missing-persona")

        self.assertEqual("persona_not_found", captured.exception.code)

    def test_wrong_owner_cannot_read_or_delete_persona_learning(self) -> None:
        self.service.save_style_profile(self.owner_id, self.persona.id, self._profile())

        with self.assertRaises(LearningServiceError) as captured:
            self.service.get_style_profile("other-owner", self.persona.id)
        self.assertEqual("persona_not_found", captured.exception.code)

        with self.assertRaises(LearningServiceError) as captured:
            self.service.delete_for_persona("other-owner", self.persona.id)
        self.assertEqual("persona_not_found", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
