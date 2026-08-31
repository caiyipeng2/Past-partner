from __future__ import annotations

import base64
import http.client
import json
import os
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR


class HttpBillingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        key = base64.b64encode(b"d" * MASTER_KEY_BYTES).decode("ascii")
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

    def test_read_routes_expose_balance_and_redacted_history(self) -> None:
        self.application.billing.credit(
            self.application.auth.owner_id,
            amount_minor=1500,
            currency="CNY",
            operation_key="payment-1",
        )
        self.application.billing.debit(
            self.application.auth.owner_id,
            amount_minor=250,
            currency="CNY",
            operation_key="usage-1",
        )

        status, balance = self.request("GET", "/api/v1/billing/balance?currency=CNY", self.read_token)
        self.assertEqual(200, status)
        self.assertEqual({"currency": "CNY", "balance_minor": 1250}, balance)

        status, entries = self.request("GET", "/api/v1/billing/entries?limit=1", self.read_token)
        self.assertEqual(200, status)
        self.assertEqual(1, len(entries["entries"]))
        self.assertEqual("debit", entries["entries"][0]["direction"])
        self.assertNotIn("operation_key", entries["entries"][0])
        self.assertIn("next_cursor", entries)

    def test_billing_routes_require_read_scope_and_never_accept_mutations(self) -> None:
        status, payload = self.request("GET", "/api/v1/billing/balance?currency=CNY", self.write_token)
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", payload["error"]["code"])

        status, payload = self.request("POST", "/api/v1/billing/entries", self.full_token)
        self.assertEqual(404, status)
        self.assertEqual("route_not_found", payload["error"]["code"])

    def test_entry_pagination_rejects_invalid_limit_and_cursor(self) -> None:
        status, payload = self.request("GET", "/api/v1/billing/entries?limit=101", self.read_token)
        self.assertEqual(400, status)
        self.assertEqual("invalid_billing_limit", payload["error"]["code"])

        status, payload = self.request("GET", "/api/v1/billing/entries?before=invalid", self.read_token)
        self.assertEqual(400, status)
        self.assertEqual("invalid_billing_cursor", payload["error"]["code"])

    def test_balance_requires_an_iso_currency_and_rejects_account_mismatch(self) -> None:
        self.application.billing.credit(
            self.application.auth.owner_id,
            amount_minor=100,
            currency="CNY",
            operation_key="payment-1",
        )

        status, payload = self.request("GET", "/api/v1/billing/balance", self.read_token)
        self.assertEqual(400, status)
        self.assertEqual("missing_billing_currency", payload["error"]["code"])

        status, payload = self.request("GET", "/api/v1/billing/balance?currency=EUR", self.read_token)
        self.assertEqual(409, status)
        self.assertEqual("billing_currency_mismatch", payload["error"]["code"])

    def test_owner_deletion_cascades_billing_entries(self) -> None:
        owner_id = self.application.auth.owner_id
        self.application.billing.credit(
            owner_id,
            amount_minor=100,
            currency="CNY",
            operation_key="payment-1",
        )

        result = self.application.delete_owner_data(owner_id, {"confirm": "DELETE"})
        self.assertTrue(result["deleted"])
        self.assertEqual(0, self.application.billing.balance(owner_id, "CNY")["balance_minor"])

    def test_owner_export_includes_billing_metadata_without_operation_keys(self) -> None:
        owner_id = self.application.auth.owner_id
        self.application.billing.credit(
            owner_id,
            amount_minor=100,
            currency="CNY",
            operation_key="payment-1",
        )

        exported = self.application.export_data(owner_id)

        self.assertEqual(100, exported["billing"]["balances"][0]["balance_minor"])
        self.assertEqual(1, len(exported["billing"]["entries"]))
        self.assertNotIn("operation_key", exported["billing"]["entries"][0])

    def test_owner_export_walks_all_billing_pages(self) -> None:
        owner_id = self.application.auth.owner_id
        for index in range(101):
            self.application.billing.credit(
                owner_id,
                amount_minor=1,
                currency="CNY",
                operation_key=f"payment-{index}",
            )

        exported = self.application.export_data(owner_id)

        self.assertEqual(101, len(exported["billing"]["entries"]))
        self.assertEqual(101, exported["billing"]["balances"][0]["balance_minor"])


if __name__ == "__main__":
    unittest.main()
