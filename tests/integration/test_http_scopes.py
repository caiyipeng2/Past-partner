import http.client
import json
import shutil
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server


class HttpScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="development",
        )
        self.application = Application.from_config(config)
        self.server = create_server(config, self.application)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

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

    def test_read_scope_can_read_but_cannot_mutate(self) -> None:
        session = self.application.auth.issue_session("127.0.0.1", scopes=["owner:read"])
        token = session["access_token"]

        status, payload = self.request("GET", "/api/v1/personas", token)
        self.assertEqual(200, status)
        self.assertEqual([], payload["personas"])

        status, payload = self.request(
            "POST",
            "/api/v1/personas",
            token,
            {"display_name": "受限", "relationship_type": "friend"},
        )
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", payload["error"]["code"])

    def test_write_scope_can_mutate_but_cannot_read(self) -> None:
        session = self.application.auth.issue_session("127.0.0.1", scopes=["owner:write"])
        token = session["access_token"]

        status, payload = self.request("GET", "/api/v1/personas", token)
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", payload["error"]["code"])

        status, payload = self.request(
            "POST",
            "/api/v1/personas",
            token,
            {"display_name": "写入", "relationship_type": "friend"},
        )
        self.assertEqual(201, status)
        self.assertEqual("写入", payload["display_name"])


if __name__ == "__main__":
    unittest.main()
