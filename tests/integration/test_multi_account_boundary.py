import http.client
import base64
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


class MultiAccountBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.environment = patch.dict(
            os.environ,
            {MASTER_KEY_ENV_VAR: base64.b64encode(b"r" * MASTER_KEY_BYTES).decode("ascii")},
        )
        self.environment.start()
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        self.application = Application.from_config(config)
        self.server = create_server(config, self.application)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        first = self.application.auth.create_local_account(
            "oidc:account-a", tenant_id="tenant-a", role="member"
        )
        second = self.application.auth.create_local_account(
            "oidc:account-b", tenant_id="tenant-b", role="member"
        )
        self.first_session = self.application.auth.issue_account_session(first["user_id"])
        self.second_session = self.application.auth.issue_account_session(second["user_id"])

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.data_root, ignore_errors=True)
        self.environment.stop()

    def request(self, method: str, path: str, token: str, body: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {token}"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        decoded = json.loads(raw) if raw and "application/json" in response_headers.get("Content-Type", "") else raw
        return response.status, decoded

    def test_accounts_cannot_read_or_delete_each_others_personas(self) -> None:
        first_token = self.first_session["access_token"]
        second_token = self.second_session["access_token"]

        status, persona = self.request(
            "POST",
            "/api/v1/personas",
            first_token,
            {"display_name": "Account A", "relationship_type": "friend"},
        )
        self.assertEqual(201, status)

        status, own = self.request("GET", f"/api/v1/personas/{persona['id']}", first_token)
        self.assertEqual(200, status)
        self.assertEqual(persona["id"], own["id"])

        status, hidden = self.request("GET", f"/api/v1/personas/{persona['id']}", second_token)
        self.assertEqual(404, status)
        self.assertEqual("not_found", hidden["error"]["code"])

        status, denied_delete = self.request(
            "DELETE", f"/api/v1/personas/{persona['id']}", second_token
        )
        self.assertEqual(404, status)
        self.assertEqual("not_found", denied_delete["error"]["code"])

        status, still_owned = self.request("GET", f"/api/v1/personas/{persona['id']}", first_token)
        self.assertEqual(200, status)
        self.assertEqual(persona["id"], still_owned["id"])

        database_bytes = (self.data_root / "database" / "past-partner.sqlite3").read_bytes()
        self.assertNotIn(first_token.encode("utf-8"), database_bytes)
        self.assertNotIn(second_token.encode("utf-8"), database_bytes)
        self.assertNotIn("encrypted_payload", json.dumps(own))


if __name__ == "__main__":
    unittest.main()
