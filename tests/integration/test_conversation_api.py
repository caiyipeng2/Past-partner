import http.client
import base64
import json
import os
import shutil
import threading
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server


class ConversationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        key = base64.b64encode(b"t" * 32).decode("ascii")
        with patch.dict(os.environ, {"PAST_PARTNER_MASTER_KEY": key}):
            application = Application.from_config(config)
        self.server = create_server(config, application)
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
        shutil.rmtree(self.data_root, ignore_errors=True)

    def request(self, method: str, path: str, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = dict(headers or {})
        if getattr(self, "auth_token", None) and "Authorization" not in request_headers:
            request_headers["Authorization"] = f"Bearer {self.auth_token}"
        encoded = None
        if isinstance(body, dict):
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        content_type = response.getheader("Content-Type", "")
        connection.close()
        payload = json.loads(raw) if raw and "application/json" in content_type else raw
        return response.status, dict(response.getheaders()), payload

    def test_create_send_and_reload_persona_scoped_history(self) -> None:
        status, _, persona = self.request(
            "POST", "/api/v1/personas", {"display_name": "小雅", "relationship_type": "friend"}
        )
        self.assertEqual(201, status)
        status, _, created = self.request(
            "POST",
            "/api/v1/conversations",
            {"persona_id": persona["id"], "provider_id": "test", "model_id": "deterministic"},
        )
        self.assertEqual(201, status)
        self.assertEqual([], created["messages"])

        status, _, sent = self.request(
            "POST",
            f"/api/v1/conversations/{created['id']}/messages",
            {"content": "你还好吗？"},
        )
        self.assertEqual(200, status)
        self.assertEqual(["user", "assistant"], [item["role"] for item in sent["messages"]])
        self.assertEqual("测试回复：你还好吗？", sent["messages"][-1]["content"])

        status, _, loaded = self.request("GET", f"/api/v1/conversations/{created['id']}")
        self.assertEqual(200, status)
        self.assertEqual(sent, loaded)
        status, _, listed = self.request(
            "GET", f"/api/v1/conversations?persona_id={persona['id']}"
        )
        self.assertEqual(200, status)
        self.assertEqual([created["id"]], [item["id"] for item in listed["conversations"]])

    def test_empty_message_and_unknown_conversation_are_stable_errors(self) -> None:
        status, _, payload = self.request("GET", "/api/v1/conversations/not-found")
        self.assertEqual(404, status)
        self.assertEqual("not_found", payload["error"]["code"])
        status, _, payload = self.request(
            "POST", "/api/v1/conversations/not-found/messages", {"content": " "}
        )
        self.assertEqual(404, status)
        self.assertEqual("not_found", payload["error"]["code"])

    def test_provider_failure_does_not_persist_user_message(self) -> None:
        status, _, persona = self.request(
            "POST", "/api/v1/personas", {"display_name": "小雨", "relationship_type": "friend"}
        )
        self.assertEqual(201, status)
        status, _, created = self.request(
            "POST",
            "/api/v1/conversations",
            {"persona_id": persona["id"], "provider_id": "deepseek", "model_id": "deepseek-v4-flash"},
        )
        self.assertEqual(201, status)
        status, _, payload = self.request(
            "POST", f"/api/v1/conversations/{created['id']}/messages", {"content": "你好"}
        )
        self.assertEqual(503, status)
        self.assertEqual("provider_not_configured", payload["error"]["code"])
        status, _, loaded = self.request("GET", f"/api/v1/conversations/{created['id']}")
        self.assertEqual(200, status)
        self.assertEqual([], loaded["messages"])

    def test_persona_delete_cascades_conversations(self) -> None:
        status, _, persona = self.request(
            "POST", "/api/v1/personas", {"display_name": "小雅", "relationship_type": "friend"}
        )
        self.assertEqual(201, status)
        status, _, conversation = self.request(
            "POST",
            "/api/v1/conversations",
            {"persona_id": persona["id"], "provider_id": "test", "model_id": "deterministic"},
        )
        self.assertEqual(201, status)
        status, _, _ = self.request("DELETE", f"/api/v1/personas/{persona['id']}")
        self.assertEqual(200, status)
        status, _, payload = self.request("GET", f"/api/v1/conversations/{conversation['id']}")
        self.assertEqual(404, status)
        self.assertEqual("not_found", payload["error"]["code"])
