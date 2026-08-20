from __future__ import annotations

import base64
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


class HttpUsageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        key = base64.b64encode(b"g" * MASTER_KEY_BYTES).decode("ascii")
        with patch.dict(os.environ, {MASTER_KEY_ENV_VAR: key}):
            self.application = Application.from_config(config)
        self.server = create_server(config, self.application)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.full_token = self.application.auth.issue_session("127.0.0.1")["access_token"]
        self.read_token = self.application.auth.issue_session(
            "127.0.0.1", scopes=["owner:read"]
        )["access_token"]
        self.write_token = self.application.auth.issue_session(
            "127.0.0.1", scopes=["owner:write"]
        )["access_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.application.close()
        shutil.rmtree(self.data_root, ignore_errors=True)

    def request(self, method: str, path: str, token: str, body: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": f"Bearer {token}"}
        encoded = None
        if body is not None:
            encoded = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        content_type = response.getheader("Content-Type", "")
        connection.close()
        return response.status, json.loads(payload) if payload and "application/json" in content_type else payload

    def _create_and_send_chat(self) -> None:
        status, persona = self.request(
            "POST",
            "/api/v1/personas",
            self.full_token,
            {"display_name": "usage", "relationship_type": "friend"},
        )
        self.assertEqual(201, status)
        status, conversation = self.request(
            "POST",
            "/api/v1/conversations",
            self.full_token,
            {"persona_id": persona["id"], "provider_id": "test", "model_id": "deterministic"},
        )
        self.assertEqual(201, status)
        status, _ = self.request(
            "POST",
            f"/api/v1/conversations/{conversation['id']}/messages",
            self.full_token,
            {"content": "hello"},
        )
        self.assertEqual(200, status)

    def test_successful_chat_usage_is_owner_scoped_redacted_and_paginated(self) -> None:
        self._create_and_send_chat()

        status, payload = self.request("GET", "/api/v1/usage?limit=1", self.read_token)
        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["usage_records"]))
        record = payload["usage_records"][0]
        self.assertEqual("chat", record["operation"])
        self.assertEqual("priced", record["status"])
        self.assertEqual(0, record["platform_charge"])
        self.assertNotIn("provider_request_fingerprint", record)
        self.assertNotIn("encrypted_payload", record)
        self.assertIn("next_cursor", payload)

        status, second = self.request(
            "GET", f"/api/v1/usage?before={payload['next_cursor']}", self.read_token
        )
        self.assertEqual(200, status)
        self.assertEqual([], second["usage_records"])

    def test_usage_read_requires_read_scope_and_rejects_mutation(self) -> None:
        status, payload = self.request("GET", "/api/v1/usage", self.write_token)
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", payload["error"]["code"])

        status, payload = self.request("POST", "/api/v1/usage", self.full_token, {})
        self.assertEqual(404, status)
        self.assertEqual("route_not_found", payload["error"]["code"])

    def test_stateless_chat_also_records_usage_for_the_authenticated_owner(self) -> None:
        status, payload = self.request(
            "POST",
            "/api/v1/chat",
            self.full_token,
            {
                "provider_id": "test",
                "model_id": "deterministic",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        self.assertEqual(200, status)
        self.assertEqual("test", payload["provider_id"])

        status, usage = self.request("GET", "/api/v1/usage", self.read_token)
        self.assertEqual(200, status)
        self.assertEqual(1, len(usage["usage_records"]))
        self.assertEqual("chat", usage["usage_records"][0]["operation"])

    def test_invalid_usage_pagination_is_stable(self) -> None:
        status, payload = self.request("GET", "/api/v1/usage?limit=101", self.full_token)
        self.assertEqual(400, status)
        self.assertEqual("invalid_usage_limit", payload["error"]["code"])

        status, payload = self.request("GET", "/api/v1/usage?before=invalid", self.full_token)
        self.assertEqual(400, status)
        self.assertEqual("invalid_usage_cursor", payload["error"]["code"])


if __name__ == "__main__":
    unittest.main()
