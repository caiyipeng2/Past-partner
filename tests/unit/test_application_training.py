"""Application wiring for the test-only capability-gated training lifecycle."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR


class ApplicationTrainingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.environment = patch.dict(
            os.environ,
            {MASTER_KEY_ENV_VAR: base64.b64encode(b"a" * MASTER_KEY_BYTES).decode("ascii")},
        )
        self.environment.start()
        config = ServerConfig(
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        self.application = Application.from_config(config)
        self.owner_id = self.application.auth.owner_id
        self.persona = self.application.create_persona(
            self.owner_id,
            {"display_name": "小雨", "relationship_type": "friend"},
        )

    def tearDown(self) -> None:
        self.environment.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _accepted_import(self) -> str:
        payload = b"".join(
            json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
            for message in (
                {
                    "sender": "persona",
                    "message": "第一条人物消息",
                    "timestamp": "2026-08-11T10:00:00+08:00",
                },
                {
                    "sender": "persona",
                    "message": "第二条人物消息",
                    "timestamp": "2026-08-11T10:01:00+08:00",
                },
            )
        )
        imported = self.application.create_import(
            self.owner_id,
            {
                "persona_id": self.persona["id"],
                "source_name": "chat.jsonl",
                "total_bytes": len(payload),
                "media_type": "application/x-ndjson",
            },
        )
        digest = hashlib.sha256(payload).hexdigest()
        self.application.put_chunk(
            self.owner_id,
            imported["id"],
            0,
            len(payload),
            digest,
            io.BytesIO(payload),
        )
        self.application.complete_import(self.owner_id, imported["id"], {"sha256": digest})
        self.application.set_participant_mapping(
            self.owner_id,
            imported["id"],
            {"mapping": {"persona": "persona"}},
        )
        preview = self.application.preview_import(self.owner_id, imported["id"], 100)
        self.application.save_import_corrections(
            self.owner_id,
            imported["id"],
            {
                "corrections": [
                    {"record_id": record["record_id"], "review_state": "accepted", "fields": {}}
                    for record in preview["records"]
                ]
            },
        )
        return imported["id"]

    def test_test_mode_wires_redacted_estimate_and_verified_training_job(self) -> None:
        import_id = self._accepted_import()

        estimate = self.application.estimate_training_job(
            self.owner_id,
            {
                "persona_id": self.persona["id"],
                "import_id": import_id,
                "provider_id": "test",
                "model_id": "deterministic",
            },
        )
        self.assertEqual(2, estimate["sample_count"])
        self.assertNotIn("content", estimate)
        consent = self.application.create_consent(
            self.owner_id,
            {
                "persona_id": self.persona["id"],
                "provider_id": "test",
                "model_id": "deterministic",
                "data_category": "persona_text",
                "estimated_cost": estimate["estimated_cost"] + 1,
                "purpose": "fine_tuning",
                "authorization_scope": f"fine_tuning:{import_id}",
            },
        )

        running = self.application.create_training_job(
            self.owner_id,
            {
                "persona_id": self.persona["id"],
                "import_id": import_id,
                "provider_id": "test",
                "model_id": "deterministic",
                "consent_id": consent["id"],
            },
        )
        completed = self.application.get_training_job(self.owner_id, running["id"])

        self.assertEqual("running", running["state"])
        self.assertEqual("completed", completed["state"])
        self.assertTrue(completed["artifact_id"])
        self.assertTrue(completed["diagnostic_id"])


if __name__ == "__main__":
    unittest.main()
