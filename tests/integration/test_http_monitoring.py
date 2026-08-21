import base64
import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR


class HttpMonitoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path(tempfile.mkdtemp(dir=Path.cwd()))
        self.environment = patch.dict(
            os.environ,
            {MASTER_KEY_ENV_VAR: base64.b64encode(b"m" * MASTER_KEY_BYTES).decode("ascii")},
        )
        self.environment.start()
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        self.server = create_server(config, Application.from_config(config))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        status, _, session = self.request("POST", "/api/v1/auth/session", include_auth=False)
        self.assertEqual(201, status)
        self.auth_token = session["access_token"]
        self.owner_id = session["owner_id"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.environment.stop()
        shutil.rmtree(self.data_root, ignore_errors=True)

    def request(self, method, path, *, include_auth=True, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = dict(headers or {})
        if include_auth:
            request_headers.setdefault("Authorization", f"Bearer {self.auth_token}")
        connection.request(method, path, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        if "application/json" in response_headers.get("Content-Type", ""):
            payload = json.loads(payload)
        return response.status, response_headers, payload

    def test_ready_endpoint_reports_metadata_liveness_without_auth(self) -> None:
        status, headers, payload = self.request("GET", "/api/v1/ready", include_auth=False)

        self.assertEqual(200, status)
        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual("ready", payload["status"])
        self.assertEqual("ok", payload["checks"]["metadata_store"])

    def test_ready_endpoint_returns_redacted_503_when_metadata_is_unavailable(self) -> None:
        class FailingStore:
            backend_name = "sqlite"

            def connect(self):
                raise RuntimeError("secret path should never escape")

            def close(self):
                return None

        self.server.application.metadata_store = FailingStore()

        status, _, payload = self.request("GET", "/api/v1/ready", include_auth=False)

        self.assertEqual(503, status)
        self.assertEqual("not_ready", payload["status"])
        self.assertEqual("unavailable", payload["checks"]["metadata_store"])
        self.assertNotIn("secret path", json.dumps(payload))

    def test_metrics_is_owner_read_protected_and_contains_redacted_request_counts(self) -> None:
        status, _, _ = self.request("GET", "/api/v1/metrics", include_auth=False)
        self.assertEqual(401, status)

        self.request("GET", "/api/v1/health")
        status, headers, payload = self.request("GET", f"/api/v1/not-found-{uuid4()}")
        self.assertEqual(404, status)
        self.assertEqual("route_not_found", payload["error"]["code"])

        status, headers, metrics = self.request("GET", "/api/v1/metrics")

        self.assertEqual(200, status)
        self.assertIn("text/plain; version=0.0.4", headers["Content-Type"])
        self.assertIn("past_partner_http_requests_total", metrics.decode("utf-8"))
        self.assertIn('route="/api/v1/health"', metrics.decode("utf-8"))
        self.assertIn('status="404"', metrics.decode("utf-8"))
        self.assertNotIn(self.auth_token, metrics.decode("utf-8"))
        self.assertNotIn(self.owner_id, metrics.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
