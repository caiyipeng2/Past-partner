"""R2-04 Task 5 redacted operations summary contracts."""

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


class HttpOperationsSummaryTests(unittest.TestCase):
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
        self.auth_token: str | None = None
        status, _, session = self.request("POST", "/api/v1/auth/session")
        self.assertEqual(201, status)
        self.auth_token = session["access_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.application.close()
        shutil.rmtree(self.data_root, ignore_errors=True)

    def request(self, method: str, path: str, body=None, *, token: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        active_token = self.auth_token if token is None else token
        if active_token:
            headers["Authorization"] = f"Bearer {active_token}"
        encoded = None
        if isinstance(body, dict):
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=headers)
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

    def test_operations_summary_is_admin_only_and_redacted(self) -> None:
        admin = self.application.auth.create_local_account(
            "operations-admin", tenant_id="operations", role="admin"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])

        status, _, payload = self.request(
            "GET", "/api/v1/operations/summary", token=admin_session["access_token"]
        )

        self.assertEqual(200, status)
        self.assertEqual("ok", payload["status"])
        self.assertEqual("admin", payload["access"]["role"])
        self.assertIn("queue", payload)
        self.assertIn("billing", payload)
        self.assertEqual("local_ledger_only", payload["billing"]["reconciliation"])
        self.assertIn("audit", payload)
        self.assertIn("notifications", payload)
        self.assertIn("diagnostic_ids", payload)
        self.assertLessEqual(len(payload["diagnostic_ids"]), 20)
        self.assertNotIn("owner_id", json.dumps(payload))
        self.assertNotIn("encrypted_payload", json.dumps(payload))
        self.assertNotIn("raw_message", json.dumps(payload))

    def test_operations_summary_rejects_member_and_unauthenticated_requests(self) -> None:
        member = self.application.auth.create_local_account(
            "operations-member", tenant_id="operations", role="member"
        )
        member_session = self.application.auth.issue_account_session(member["user_id"])

        status, _, payload = self.request(
            "GET", "/api/v1/operations/summary", token=member_session["access_token"]
        )
        self.assertEqual(403, status)
        self.assertEqual("operations_admin_required", payload["error"]["code"])

        status, _, payload = self.request(
            "GET", "/api/v1/operations/summary", token=""
        )
        self.assertEqual(401, status)
        self.assertEqual("authentication_required", payload["error"]["code"])

    def test_operations_summary_has_no_mutation_route(self) -> None:
        admin = self.application.auth.create_local_account(
            "operations-admin-write", tenant_id="operations", role="admin"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])

        status, _, payload = self.request(
            "POST",
            "/api/v1/operations/summary",
            {},
            token=admin_session["access_token"],
        )

        self.assertEqual(404, status)
        self.assertEqual("route_not_found", payload["error"]["code"])

    def test_operations_summary_aggregates_queue_and_notification_states(self) -> None:
        owner_id = self.application.auth.owner_id
        self.application.task_queue.enqueue(
            owner_id,
            "worker.probe",
            {"internal": "bounded"},
        )
        notification = self.application.notifications.record_export(
            owner_id,
            operation_id="export-ops-1",
            counts={"personas": 1},
        )
        self.application.notifications.mark_failed(
            owner_id,
            notification.id,
            error_code="provider_timeout",
            next_attempt_at="2026-09-04T00:00:00+00:00",
        )
        admin = self.application.auth.create_local_account(
            "operations-aggregate-admin", tenant_id="operations", role="admin"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])

        status, _, payload = self.request(
            "GET", "/api/v1/operations/summary", token=admin_session["access_token"]
        )

        self.assertEqual(200, status)
        self.assertEqual(1, payload["queue"]["states"]["queued"])
        self.assertEqual(1, payload["notifications"]["failed"])
        self.assertEqual("ok", payload["audit"]["status"])


if __name__ == "__main__":
    unittest.main()
