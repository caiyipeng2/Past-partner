from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import sqlite3
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR
from src.services.metadata_store import MetadataOperationalError


class HttpAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="development",
        )
        key = base64.b64encode(b"h" * MASTER_KEY_BYTES).decode("ascii")
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

    def create_and_delete_persona(self, display_name: str) -> None:
        status, persona = self.request(
            "POST",
            "/api/v1/personas",
            self.full_token,
            {"display_name": display_name, "relationship_type": "friend"},
        )
        self.assertEqual(201, status)
        status, _ = self.request("DELETE", f"/api/v1/personas/{persona['id']}", self.full_token)
        self.assertEqual(200, status)

    def test_read_scope_can_list_audit_events_but_write_scope_cannot(self) -> None:
        status, payload = self.request("GET", "/api/v1/audit-events", self.read_token)
        self.assertEqual(200, status)
        self.assertEqual([], payload["audit_events"])

        status, payload = self.request("GET", "/api/v1/audit-events", self.write_token)
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", payload["error"]["code"])

    def test_list_supports_limit_cursor_and_returns_redacted_events(self) -> None:
        self.create_and_delete_persona("一次")
        self.create_and_delete_persona("二次")

        status, payload = self.request("GET", "/api/v1/audit-events?limit=1", self.full_token)
        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["audit_events"]))
        self.assertIn("next_cursor", payload)
        event = payload["audit_events"][0]
        self.assertEqual("persona_deleted", event["action"])
        self.assertNotIn("encrypted_payload", event)
        self.assertNotIn("一次", json.dumps(payload, ensure_ascii=False))

        status, payload = self.request(
            "GET", f"/api/v1/audit-events?limit=1&before={payload['next_cursor']}", self.full_token
        )
        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["audit_events"]))

    def test_invalid_limit_and_cursor_are_stable_client_errors(self) -> None:
        status, payload = self.request("GET", "/api/v1/audit-events?limit=101", self.full_token)
        self.assertEqual(400, status)
        self.assertEqual("invalid_audit_limit", payload["error"]["code"])

        status, payload = self.request("GET", "/api/v1/audit-events?before=not-a-cursor", self.full_token)
        self.assertEqual(400, status)
        self.assertEqual("invalid_audit_cursor", payload["error"]["code"])

    def test_audit_events_is_read_only(self) -> None:
        for method in ("POST", "PATCH", "DELETE"):
            status, payload = self.request(method, "/api/v1/audit-events", self.full_token)
            self.assertEqual(404, status)
            self.assertEqual("route_not_found", payload["error"]["code"])

    def test_audit_read_surfaces_chain_mismatch_as_stable_operational_error(self) -> None:
        self.create_and_delete_persona("链校验")
        with sqlite3.connect(self.application.auth.database_path) as connection:
            connection.execute(
                "UPDATE audit_events SET event_hash = ?",
                ("e" * 64,),
            )

        status, payload = self.request("GET", "/api/v1/audit-events", self.read_token)

        self.assertEqual(503, status)
        self.assertEqual("audit_chain_mismatch", payload["error"]["code"])

    def test_audit_backend_failure_is_stable_and_redacted(self) -> None:
        with patch.object(
            self.application.audit_repository,
            "verify",
            side_effect=MetadataOperationalError(),
        ):
            status, payload = self.request("GET", "/api/v1/audit-events", self.read_token)

        self.assertEqual(503, status)
        self.assertEqual("audit_unavailable", payload["error"]["code"])


if __name__ == "__main__":
    unittest.main()
