from __future__ import annotations

import base64
import http.client
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR


class HttpSubscriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        key = base64.b64encode(b"v" * MASTER_KEY_BYTES).decode("ascii")
        with patch.dict(os.environ, {MASTER_KEY_ENV_VAR: key}):
            self.application = Application.from_config(config)
        self.server = create_server(config, self.application)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.full_token = self.application.auth.issue_session("127.0.0.1")["access_token"]
        self.read_token = self.application.auth.issue_session("127.0.0.1", scopes=["owner:read"])["access_token"]
        self.write_token = self.application.auth.issue_session("127.0.0.1", scopes=["owner:write"])["access_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.application.close()
        shutil.rmtree(self.data_root, ignore_errors=True)

    def request(self, method: str, path: str, token: str):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, headers={"Authorization": f"Bearer {token}"})
        response = connection.getresponse()
        payload = response.read()
        content_type = response.getheader("Content-Type", "")
        connection.close()
        return response.status, json.loads(payload) if payload and "application/json" in content_type else payload

    def test_subscription_read_route_returns_empty_or_current_entitlement(self) -> None:
        status, empty = self.request("GET", "/api/v1/subscription", self.read_token)
        self.assertEqual(200, status)
        self.assertEqual({"subscription": None, "entitled": False}, empty)

        self.application.subscriptions.apply_provider_event(
            self.application.auth.owner_id,
            provider_id="stripe",
            provider_event_key="evt-1",
            provider_subscription_id="sub-1",
            plan_id="plus-monthly",
            status="trial",
            current_period_start="2026-08-01T00:00:00+00:00",
            current_period_end="2026-09-01T00:00:00+00:00",
            occurred_at="2026-08-01T00:00:00+00:00",
            signature_verified=True,
        )
        status, current = self.request("GET", "/api/v1/subscription", self.read_token)
        self.assertEqual(200, status)
        self.assertEqual("trial", current["subscription"]["status"])
        self.assertNotIn("provider_event_key", current["subscription"])

    def test_subscription_route_requires_read_scope_and_has_no_client_mutation(self) -> None:
        status, payload = self.request("GET", "/api/v1/subscription", self.write_token)
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", payload["error"]["code"])

        status, payload = self.request("POST", "/api/v1/subscription", self.full_token)
        self.assertEqual(404, status)
        self.assertEqual("route_not_found", payload["error"]["code"])

    def test_owner_export_includes_subscription_metadata(self) -> None:
        owner_id = self.application.auth.owner_id
        self.application.subscriptions.apply_provider_event(
            owner_id,
            provider_id="stripe",
            provider_event_key="evt-1",
            provider_subscription_id="sub-1",
            plan_id="plus-monthly",
            status="active",
            current_period_start="2026-08-01T00:00:00+00:00",
            current_period_end="2026-09-01T00:00:00+00:00",
            occurred_at="2026-08-01T00:00:00+00:00",
            signature_verified=True,
        )

        exported = self.application.export_data(owner_id)

        self.assertEqual("active", exported["subscription"]["subscription"]["status"])
        self.assertNotIn("provider_event_key", json.dumps(exported, ensure_ascii=False))

    def test_owner_deletion_cascades_subscription_snapshot_events_and_bindings(self) -> None:
        owner_id = self.application.auth.owner_id
        self.application.subscriptions.apply_provider_event(
            owner_id,
            provider_id="stripe",
            provider_event_key="evt-1",
            provider_subscription_id="sub-1",
            plan_id="plus-monthly",
            status="active",
            current_period_start="2026-08-01T00:00:00+00:00",
            current_period_end="2026-09-01T00:00:00+00:00",
            occurred_at="2026-08-01T00:00:00+00:00",
            signature_verified=True,
        )

        self.application.delete_owner_data(owner_id, {"confirm": "DELETE"})

        with sqlite3.connect(self.application.auth.database_path) as connection:
            for table in ("subscriptions", "subscription_events", "subscription_bindings"):
                self.assertEqual(0, connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
