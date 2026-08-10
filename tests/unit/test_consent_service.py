"""P1-10 media consent persistence and scope contracts."""

from __future__ import annotations

import base64
import shutil
import sqlite3
import unittest
from pathlib import Path
from uuid import uuid4

from src.domain.consents import ConsentValidationError
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.consent_repository import ConsentRepository
from src.services.consent_service import ConsentService
from src.services.master_key import (
    EnvironmentMasterKeyProvider,
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
)
from src.services.local_auth import LocalAuthService
from src.services.persona_repository import PersonaRepository
from src.services.persona_service import PersonaService
from src.services.storage import StorageLayout


class ConsentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"c" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.personas = PersonaService(PersonaRepository(self.layout.database_path(), self.encryption))
        self.auth = LocalAuthService(self.layout.database_path(), self.encryption, mode="test")
        self.consents = ConsentService(
            ConsentRepository(self.layout.database_path(), self.encryption),
            self.personas,
        )
        self.owner_id = self.auth.owner_id
        self.persona = self.personas.create(self.owner_id, "小雨", "friend")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_creates_encrypted_consent_and_authorizes_exact_scope(self) -> None:
        consent = self.consents.create(
            owner_id=self.owner_id,
            persona_id=self.persona.id,
            provider_id="deepseek",
            model_id="deepseek-chat",
            data_category="image",
            estimated_cost=0.12,
            purpose="生成图片描述",
            authorization_scope="persona-image-analysis",
        )

        self.assertEqual("active", consent.status)
        self.assertEqual(consent, self.consents.authorize(
            self.owner_id,
            consent.id,
            provider_id="deepseek",
            model_id="deepseek-chat",
            data_category="image",
            authorization_scope="persona-image-analysis",
        ))
        database_bytes = self.layout.database_path().read_bytes()
        self.assertNotIn("生成图片描述".encode("utf-8"), database_bytes)
        with sqlite3.connect(self.layout.database_path()) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'consents'"
            ).fetchone()
        self.assertEqual(("consents",), table)

    def test_rejects_duplicate_scope_and_mismatched_authorization(self) -> None:
        consent = self.consents.create(
            self.owner_id,
            self.persona.id,
            "qwen",
            "qwen-plus",
            "audio",
            0,
            "语音转写",
            "persona-audio-transcription",
        )

        with self.assertRaises(ConsentValidationError) as duplicate:
            self.consents.create(
                self.owner_id,
                self.persona.id,
                "qwen",
                "qwen-plus",
                "audio",
                0,
                "语音转写",
                "persona-audio-transcription",
            )
        self.assertEqual("consent_exists", duplicate.exception.code)

        with self.assertRaises(ConsentValidationError) as mismatch:
            self.consents.authorize(
                self.owner_id,
                consent.id,
                provider_id="qwen",
                model_id="qwen-plus",
                data_category="image",
                authorization_scope="persona-audio-transcription",
            )
        self.assertEqual("consent_scope_mismatch", mismatch.exception.code)

    def test_revocation_blocks_future_authorization_and_is_listed(self) -> None:
        consent = self.consents.create(
            self.owner_id,
            self.persona.id,
            "custom",
            "local-vision",
            "media",
            0,
            "本地媒体分析",
            "persona-media-analysis",
        )

        revoked = self.consents.revoke(self.owner_id, consent.id)
        self.assertEqual("revoked", revoked.status)
        self.assertIsNotNone(revoked.revoked_at)
        self.assertEqual([revoked], self.consents.list(self.owner_id, self.persona.id))
        with self.assertRaises(ConsentValidationError) as raised:
            self.consents.authorize(
                self.owner_id,
                consent.id,
                provider_id="custom",
                model_id="local-vision",
                data_category="media",
                authorization_scope="persona-media-analysis",
            )
        self.assertEqual("consent_revoked", raised.exception.code)


if __name__ == "__main__":
    unittest.main()
