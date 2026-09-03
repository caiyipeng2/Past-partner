"""R2-04 Task 4 notification HTTP lifecycle contracts."""

from __future__ import annotations

import http.client
import json
from pathlib import Path
import shutil
import threading
import unittest
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server


class HttpNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="development",
        )
        self.server = create_server(config, Application.from_config(config))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.auth_token: str | None = None
        status, _, session = self.request("POST", "/api/v1/auth/session")
        self.assertEqual(201, status)
        self.auth_token = session["access_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.server.application.close()
        shutil.rmtree(self.data_root, ignore_errors=True)

    def request(self, method: str, path: str, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = dict(headers or {})
        if self.auth_token and "Authorization" not in request_headers:
            request_headers["Authorization"] = f"Bearer {self.auth_token}"
        encoded = None
        if isinstance(body, dict):
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        payload = (
            json.loads(raw)
            if raw and "application/json" in response_headers.get("Content-Type", "")
            else raw
        )
        return response.status, response_headers, payload

    def test_export_creates_owner_notification_visible_through_bounded_route(self) -> None:
        status, _, exported = self.request("GET", "/api/v1/data-export")
        self.assertEqual(200, status)
        self.assertEqual(1, exported["export_version"])

        status, _, payload = self.request("GET", "/api/v1/notifications?limit=10")

        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["notifications"]))
        notification = payload["notifications"][0]
        self.assertEqual("export_completed", notification["event_type"])
        self.assertEqual("pending", notification["status"])
        self.assertNotIn("owner_id", notification)
        self.assertNotIn("raw_message", notification)

    def test_notification_route_returns_a_bounded_cursor_page(self) -> None:
        self.request("GET", "/api/v1/data-export")
        self.request("GET", "/api/v1/data-export")

        status, _, first = self.request("GET", "/api/v1/notifications?limit=1")
        self.assertEqual(200, status)
        self.assertEqual(1, len(first["notifications"]))
        self.assertIn("next_cursor", first)

        status, _, second = self.request(
            "GET", f"/api/v1/notifications?limit=1&before={first['next_cursor']}"
        )

        self.assertEqual(200, status)
        self.assertEqual(1, len(second["notifications"]))
        self.assertNotEqual(
            first["notifications"][0]["id"], second["notifications"][0]["id"]
        )

    def test_notification_route_requires_owner_read_authentication(self) -> None:
        token = self.auth_token
        self.auth_token = None
        status, _, payload = self.request("GET", "/api/v1/notifications")
        self.auth_token = token

        self.assertEqual(401, status)
        self.assertEqual("authentication_required", payload["error"]["code"])

    def test_owner_deletion_preserves_a_redacted_deletion_notification(self) -> None:
        status, _, deleted = self.request(
            "POST", "/api/v1/data-deletion", {"confirm": "DELETE"}
        )
        self.assertEqual(200, status)
        self.assertTrue(deleted["deleted"])

        # The deletion transaction removes the old session. Re-pair through the
        # unauthenticated development session endpoint before reading the notice.
        self.auth_token = None
        status, _, session = self.request("POST", "/api/v1/auth/session")
        self.assertEqual(201, status)
        self.auth_token = session["access_token"]
        status, _, payload = self.request("GET", "/api/v1/notifications")

        self.assertEqual(200, status)
        self.assertEqual(1, len(payload["notifications"]))
        notification = payload["notifications"][0]
        self.assertEqual("deletion_completed", notification["event_type"])
        self.assertEqual(deleted["receipt_id"], notification["operation_id"])
        self.assertEqual(0, notification["counts"]["provider_side_cleanup_limitations"])
        self.assertNotIn("owner_id", notification)


if __name__ == "__main__":
    unittest.main()
