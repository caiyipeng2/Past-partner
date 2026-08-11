"""HTTP contract tests for capability-gated fine-tuning jobs in test mode."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import shutil
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.training_service import TrainingServiceError


class HttpTrainingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.environment = patch.dict(
            os.environ,
            {MASTER_KEY_ENV_VAR: base64.b64encode(b"h" * MASTER_KEY_BYTES).decode("ascii")},
        )
        self.environment.start()
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        self.server = create_server(config, Application.from_config(config))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        status, _, session = self.request("POST", "/api/v1/auth/session")
        self.assertEqual(201, status)
        self.auth_token = session["access_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.environment.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def request(self, method: str, path: str, body: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        encoded = None
        if getattr(self, "auth_token", None):
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if body is not None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        value = json.loads(raw) if raw and "application/json" in response_headers.get("Content-Type", "") else raw
        return response.status, response_headers, value

    def _accepted_import(self, persona_id: str) -> str:
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
        status, _, imported = self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": persona_id,
                "source_name": "chat.jsonl",
                "total_bytes": len(payload),
                "media_type": "application/x-ndjson",
            },
        )
        self.assertEqual(201, status)
        digest = hashlib.sha256(payload).hexdigest()
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "PUT",
            f"/api/v1/imports/{imported['id']}/chunks/0",
            body=payload,
            headers={
                "Authorization": f"Bearer {self.auth_token}",
                "Content-Length": str(len(payload)),
                "X-Chunk-Sha256": digest,
            },
        )
        response = connection.getresponse()
        response.read()
        self.assertEqual(200, response.status)
        connection.close()
        status, _, _ = self.request(
            "POST", f"/api/v1/imports/{imported['id']}/complete", {"sha256": digest}
        )
        self.assertEqual(200, status)
        status, _, _ = self.request(
            "POST",
            f"/api/v1/imports/{imported['id']}/participant-mapping",
            {"mapping": {"persona": "persona"}},
        )
        self.assertEqual(200, status)
        status, _, preview = self.request("GET", f"/api/v1/imports/{imported['id']}/preview?limit=100")
        self.assertEqual(200, status)
        status, _, _ = self.request(
            "POST",
            f"/api/v1/imports/{imported['id']}/corrections",
            {
                "corrections": [
                    {"record_id": record["record_id"], "review_state": "accepted", "fields": {}}
                    for record in preview["records"]
                ]
            },
        )
        self.assertEqual(200, status)
        return imported["id"]

    def _consent(self, persona_id: str, import_id: str, cost: float) -> dict:
        status, _, consent = self.request(
            "POST",
            "/api/v1/consents",
            {
                "persona_id": persona_id,
                "provider_id": "test",
                "model_id": "deterministic",
                "data_category": "persona_text",
                "estimated_cost": cost,
                "purpose": "fine_tuning",
                "authorization_scope": f"fine_tuning:{import_id}",
            },
        )
        self.assertEqual(201, status)
        return consent

    def test_estimate_create_list_get_and_cancel_training_jobs(self) -> None:
        status, _, persona = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "小雨", "relationship_type": "friend"},
        )
        self.assertEqual(201, status)
        import_id = self._accepted_import(persona["id"])
        request_body = {
            "persona_id": persona["id"],
            "import_id": import_id,
            "provider_id": "test",
            "model_id": "deterministic",
        }

        status, _, estimate = self.request("POST", "/api/v1/training-jobs/estimate", request_body)
        self.assertEqual(200, status)
        self.assertEqual(2, estimate["sample_count"])
        self.assertNotIn("content", estimate)
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))
        consent = self._consent(persona["id"], import_id, estimate["estimated_cost"] + 1)

        status, _, running = self.request(
            "POST",
            "/api/v1/training-jobs",
            {**request_body, "consent_id": consent["id"]},
        )
        self.assertEqual(202, status)
        self.assertEqual("running", running["state"])
        self.assertTrue(running["diagnostic_id"])

        status, _, completed = self.request("GET", f"/api/v1/training-jobs/{running['id']}")
        self.assertEqual(200, status)
        self.assertEqual("completed", completed["state"])
        self.assertTrue(completed["artifact_id"])
        status, _, listed = self.request("GET", f"/api/v1/training-jobs?persona_id={persona['id']}")
        self.assertEqual(200, status)
        self.assertEqual([running["id"]], [item["id"] for item in listed["training_jobs"]])

        status, _, already_used = self.request(
            "POST",
            "/api/v1/training-jobs",
            {**request_body, "consent_id": consent["id"]},
        )
        self.assertEqual(409, status)
        self.assertEqual("training_consent_already_used", already_used["error"]["code"])

        status, _, _ = self.request("POST", f"/api/v1/consents/{consent['id']}/revoke")
        self.assertEqual(200, status)
        next_consent = self._consent(persona["id"], import_id, estimate["estimated_cost"] + 1)
        status, _, second = self.request(
            "POST",
            "/api/v1/training-jobs",
            {**request_body, "consent_id": next_consent["id"]},
        )
        self.assertEqual(202, status)
        status, _, cancelled = self.request(
            "POST", f"/api/v1/training-jobs/{second['id']}/cancel", {}
        )
        self.assertEqual(200, status)
        self.assertEqual("cancelled", cancelled["state"])

    def test_training_operational_failures_use_server_error_statuses(self) -> None:
        for code, expected_status in (
            ("training_dataset_cleanup_failed", 500),
            ("training_job_conflict", 409),
        ):
            with self.subTest(code=code), patch.object(
                self.server.application,
                "create_training_job",
                side_effect=TrainingServiceError(code, "training operation could not complete"),
            ):
                status, _, payload = self.request("POST", "/api/v1/training-jobs", {})

            self.assertEqual(expected_status, status)
            self.assertEqual(code, payload["error"]["code"])
            self.assertTrue(payload["error"]["diagnostic_id"])


if __name__ == "__main__":
    unittest.main()
