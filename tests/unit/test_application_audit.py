from __future__ import annotations

import base64
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.storage import StorageLayout


class ApplicationAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        key = base64.b64encode(b"p" * MASTER_KEY_BYTES).decode("ascii")
        with patch.dict(os.environ, {MASTER_KEY_ENV_VAR: key}):
            self.application = Application.from_config(config)
        self.owner_id = self.application.auth.owner_id

    def tearDown(self) -> None:
        self.application.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def create_persona(self) -> dict[str, object]:
        return self.application.create_persona(
            self.owner_id,
            {"display_name": "妈妈", "relationship_type": "mother"},
        )

    def test_persona_deletion_records_redacted_owner_event(self) -> None:
        persona = self.create_persona()

        result = self.application.delete_persona(self.owner_id, str(persona["id"]))

        self.assertTrue(result["deleted"])
        events = self.application.audit_repository.list(self.owner_id)
        self.assertEqual(1, len(events))
        self.assertEqual("persona_deleted", events[0].action.value)
        self.assertEqual(str(persona["id"]), events[0].resource_id)
        self.assertEqual({"deleted_children": 0}, dict(events[0].metadata))

    def test_import_deletion_records_only_routing_metadata(self) -> None:
        persona = self.create_persona()
        job = self.application.create_import(
            self.owner_id,
            {
                "persona_id": persona["id"],
                "source_name": "chat.txt",
                "total_bytes": 0,
                "media_type": "text/plain",
            },
        )

        self.application.delete_import(self.owner_id, str(job["id"]))

        events = self.application.audit_repository.list(self.owner_id)
        self.assertEqual("import_deleted", events[0].action.value)
        self.assertEqual(str(job["id"]), events[0].resource_id)
        self.assertEqual({}, dict(events[0].metadata))

    def test_consent_authorize_and_revoke_are_audited_for_same_owner(self) -> None:
        persona = self.create_persona()
        consent = self.application.create_consent(
            self.owner_id,
            {
                "persona_id": persona["id"],
                "provider_id": "qwen",
                "model_id": "qwen3.7-plus",
                "data_category": "image",
                "estimated_cost": 0.01,
                "purpose": "chat",
                "authorization_scope": "persona-text",
            },
        )

        self.application.authorize_consent(
            self.owner_id,
            str(consent["id"]),
            {
                "provider_id": "qwen",
                "model_id": "qwen3.7-plus",
                "data_category": "image",
                "authorization_scope": "persona-text",
            },
        )
        self.application.revoke_consent(self.owner_id, str(consent["id"]))

        events = self.application.audit_repository.list(self.owner_id)
        self.assertEqual(
            ["consent_revoked", "consent_authorized"],
            [event.action.value for event in events],
        )
        self.assertEqual("qwen", events[1].metadata["provider_id"])
        self.assertNotIn("estimated_cost", events[0].metadata)


if __name__ == "__main__":
    unittest.main()
