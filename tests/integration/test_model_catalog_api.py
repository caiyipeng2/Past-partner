"""P2-01 model metadata and cost-estimate API contracts."""

from __future__ import annotations

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


class ModelCatalogApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="development",
            model_pricing_json=json.dumps(
                {
                    "deepseek/deepseek-v4-flash": {
                        "context_length": 128000,
                        "input_price_per_million_tokens": 0.14,
                        "output_price_per_million_tokens": 0.28,
                        "currency": "USD",
                        "source": "admin",
                        "last_refreshed_at": "2026-08-10T00:00:00+00:00",
                    }
                }
            ),
        )
        self.server = create_server(config, Application.from_config(config))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.auth_token = None
        status, _, session = self.request("POST", "/api/v1/auth/session")
        self.assertEqual(201, status)
        self.auth_token = session["access_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.data_root, ignore_errors=True)

    def request(self, method: str, path: str, body: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        encoded = None
        if body is not None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        content_type = response.getheader("Content-Type", "")
        connection.close()
        return response.status, dict(response.getheaders()), json.loads(payload) if payload and "application/json" in content_type else payload

    def test_model_list_and_cost_estimate_expose_refreshable_pricing(self) -> None:
        status, _, payload = self.request("GET", "/api/v1/models?provider_id=deepseek")
        self.assertEqual(200, status)
        model = next(item for item in payload["models"] if item["id"] == "deepseek-v4-flash")
        self.assertEqual(128000, model["context_length"])
        self.assertEqual("admin", model["pricing"]["source"])
        self.assertEqual("2026-08-10T00:00:00+00:00", model["pricing"]["last_refreshed_at"])

        status, _, estimate = self.request(
            "POST",
            "/api/v1/models/cost-estimate",
            {
                "provider_id": "deepseek",
                "model_id": "deepseek-v4-flash",
                "input_tokens": 1_000_000,
                "output_tokens": 500_000,
            },
        )
        self.assertEqual(200, status)
        self.assertEqual("USD", estimate["currency"])
        self.assertAlmostEqual(0.28, estimate["estimated_cost"])
        self.assertEqual("2026-08-10T00:00:00+00:00", estimate["price_last_refreshed_at"])

    def test_cost_estimate_rejects_missing_provider_price(self) -> None:
        status, _, payload = self.request(
            "POST",
            "/api/v1/models/cost-estimate",
            {
                "provider_id": "qwen",
                "model_id": "qwen3.7-plus",
                "input_tokens": 100,
                "output_tokens": 100,
            },
        )
        self.assertEqual(422, status)
        self.assertEqual("pricing_unavailable", payload["error"]["code"])


if __name__ == "__main__":
    unittest.main()
