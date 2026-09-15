from __future__ import annotations

import http.client
import json
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server
from src.services.local_auth import LocalAuthError


class HttpTenantManagementTests(unittest.TestCase):
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

    def request(self, method: str, path: str, body=None, *, token: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        encoded = None
        if isinstance(body, dict):
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, json.loads(payload)

    def test_admin_lists_same_tenant_members_and_revokes_one_member_sessions(self) -> None:
        admin = self.application.auth.create_local_account(
            "tenant-admin-http", tenant_id="tenant-a", role="admin"
        )
        member = self.application.auth.create_local_account(
            "tenant-member-http", tenant_id="tenant-a", role="member"
        )
        other = self.application.auth.create_local_account(
            "tenant-other-http", tenant_id="tenant-b", role="member"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])
        member_session = self.application.auth.issue_account_session(member["user_id"])
        other_session = self.application.auth.issue_account_session(other["user_id"])

        status, listed = self.request(
            "GET",
            "/api/v1/tenant/members?limit=10",
            token=admin_session["access_token"],
        )
        self.assertEqual(200, status)
        self.assertEqual([member["user_id"]], [item["user_id"] for item in listed["members"]])
        self.assertNotIn("encrypted_payload", json.dumps(listed))
        self.assertNotIn(admin["user_id"], json.dumps(listed))

        status, revoked = self.request(
            "POST",
            f"/api/v1/tenant/members/{member['user_id']}/revoke-sessions",
            token=admin_session["access_token"],
        )
        self.assertEqual(200, status)
        self.assertEqual(1, revoked["revoked_sessions"])
        status, _ = self.request(
            "GET", "/api/v1/personas", token=member_session["access_token"]
        )
        self.assertEqual(401, status)
        status, _ = self.request(
            "GET", "/api/v1/personas", token=other_session["access_token"]
        )
        self.assertEqual(200, status)

    def test_member_and_read_scope_cannot_manage_tenant_members(self) -> None:
        admin = self.application.auth.create_local_account(
            "tenant-admin-scope", tenant_id="tenant-scope", role="admin"
        )
        member = self.application.auth.create_local_account(
            "tenant-member-scope", tenant_id="tenant-scope", role="member"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])
        member_session = self.application.auth.issue_account_session(member["user_id"])
        read_only = self.application.auth.issue_account_session(
            admin["user_id"], scopes=["owner:read"]
        )

        status, payload = self.request(
            "GET", "/api/v1/tenant/members", token=member_session["access_token"]
        )
        self.assertEqual(403, status)
        self.assertEqual("tenant_admin_required", payload["error"]["code"])

        status, payload = self.request(
            "POST",
            f"/api/v1/tenant/members/{member['user_id']}/revoke-sessions",
            token=read_only["access_token"],
        )
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", payload["error"]["code"])

        status, payload = self.request(
            "POST",
            f"/api/v1/tenant/members/{member['user_id']}/revoke-sessions",
            token=admin_session["access_token"],
        )
        self.assertEqual(200, status)
        self.assertEqual(1, payload["revoked_sessions"])

    def test_cross_tenant_member_target_is_not_disclosed(self) -> None:
        admin = self.application.auth.create_local_account(
            "tenant-admin-cross", tenant_id="tenant-a", role="admin"
        )
        other = self.application.auth.create_local_account(
            "tenant-member-cross", tenant_id="tenant-b", role="member"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])

        status, payload = self.request(
            "POST",
            f"/api/v1/tenant/members/{other['user_id']}/revoke-sessions",
            token=admin_session["access_token"],
        )
        self.assertEqual(404, status)
        self.assertEqual("tenant_member_not_found", payload["error"]["code"])

        status, payload = self.request(
            "POST",
            "/api/v1/tenant/members/missing-member/revoke-sessions",
            token=admin_session["access_token"],
        )
        self.assertEqual(404, status)
        self.assertEqual("tenant_member_not_found", payload["error"]["code"])

    def test_tenant_member_routes_validate_limit_and_map_backend_failure(self) -> None:
        admin = self.application.auth.create_local_account(
            "tenant-admin-errors", tenant_id="tenant-errors", role="admin"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])

        status, payload = self.request(
            "GET",
            "/api/v1/tenant/members?limit=101",
            token=admin_session["access_token"],
        )
        self.assertEqual(400, status)
        self.assertEqual("tenant_member_limit_invalid", payload["error"]["code"])

        with patch.object(
            self.application.auth,
            "list_tenant_members",
            side_effect=LocalAuthError(
                "tenant_members_unavailable", "tenant member service is unavailable"
            ),
        ):
            status, payload = self.request(
                "GET",
                "/api/v1/tenant/members",
                token=admin_session["access_token"],
            )
        self.assertEqual(503, status)
        self.assertEqual("tenant_members_unavailable", payload["error"]["code"])
        self.assertNotIn("driver detail", json.dumps(payload))

    def test_admin_can_read_and_suspend_reactivate_current_tenant(self) -> None:
        admin = self.application.auth.create_local_account(
            "tenant-registry-http-admin", tenant_id="tenant-registry-http", role="admin"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])

        status, payload = self.request("GET", "/api/v1/tenant", token=admin_session["access_token"])
        self.assertEqual(200, status)
        self.assertEqual("tenant-registry-http", payload["tenant_id"])
        self.assertEqual("active", payload["status"])

        status, payload = self.request(
            "PATCH", "/api/v1/tenant/status", {"status": "suspended"}, token=admin_session["access_token"]
        )
        self.assertEqual(200, status)
        self.assertEqual("suspended", payload["status"])

        status, payload = self.request(
            "PATCH", "/api/v1/tenant/status", {"status": "paused"}, token=admin_session["access_token"]
        )
        self.assertEqual(400, status)
        self.assertEqual("tenant_status_invalid", payload["error"]["code"])

        status, payload = self.request(
            "PATCH", "/api/v1/tenant/status", {"status": "active"}, token=admin_session["access_token"]
        )
        self.assertEqual(200, status)
        self.assertEqual("active", payload["status"])

    def test_admin_can_update_tenant_display_name_and_scope_is_required(self) -> None:
        admin = self.application.auth.create_local_account(
            "tenant-profile-http-admin", tenant_id="tenant-profile-http", role="admin"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])
        read_only = self.application.auth.issue_account_session(
            admin["user_id"], scopes=["owner:read"]
        )

        status, payload = self.request(
            "PATCH",
            "/api/v1/tenant",
            {"display_name": "  Shared memories  "},
            token=admin_session["access_token"],
        )
        self.assertEqual(200, status)
        self.assertEqual("Shared memories", payload["display_name"])
        self.assertEqual("Shared memories", self.request(
            "GET", "/api/v1/tenant", token=admin_session["access_token"]
        )[1]["display_name"])

        status, payload = self.request(
            "PATCH",
            "/api/v1/tenant",
            {"display_name": "Read-only attempt"},
            token=read_only["access_token"],
        )
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", payload["error"]["code"])

        for invalid in ({"display_name": ""}, {"display_name": "x" * 129}, {"display_name": []}):
            with self.subTest(invalid=invalid):
                status, payload = self.request(
                    "PATCH", "/api/v1/tenant", invalid, token=admin_session["access_token"]
                )
                self.assertEqual(400, status)
                self.assertEqual("tenant_display_name_invalid", payload["error"]["code"])

    def test_admin_can_disable_and_reenable_member_status(self) -> None:
        admin = self.application.auth.create_local_account(
            "tenant-status-admin", tenant_id="tenant-status", role="admin"
        )
        member = self.application.auth.create_local_account(
            "tenant-status-member", tenant_id="tenant-status", role="member"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])
        member_session = self.application.auth.issue_account_session(member["user_id"])

        status, payload = self.request(
            "PATCH",
            f"/api/v1/tenant/members/{member['user_id']}/status",
            {"status": "disabled"},
            token=admin_session["access_token"],
        )
        self.assertEqual(200, status)
        self.assertEqual("disabled", payload["account_status"])
        status, _ = self.request("GET", "/api/v1/personas", token=member_session["access_token"])
        self.assertEqual(401, status)

        status, payload = self.request(
            "PATCH",
            f"/api/v1/tenant/members/{member['user_id']}/status",
            {"status": "active"},
            token=admin_session["access_token"],
        )
        self.assertEqual(200, status)
        self.assertEqual("active", payload["account_status"])

    def test_member_status_route_rejects_read_scope_and_invalid_status(self) -> None:
        admin = self.application.auth.create_local_account(
            "tenant-status-scope-admin", tenant_id="tenant-status-scope", role="admin"
        )
        member = self.application.auth.create_local_account(
            "tenant-status-scope-member", tenant_id="tenant-status-scope", role="member"
        )
        read_only = self.application.auth.issue_account_session(
            admin["user_id"], scopes=["owner:read"]
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])

        status, payload = self.request(
            "PATCH",
            f"/api/v1/tenant/members/{member['user_id']}/status",
            {"status": "disabled"},
            token=read_only["access_token"],
        )
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", payload["error"]["code"])
        status, payload = self.request(
            "PATCH",
            f"/api/v1/tenant/members/{member['user_id']}/status",
            {"status": "paused"},
            token=admin_session["access_token"],
        )
        self.assertEqual(400, status)
        self.assertEqual("tenant_status_invalid", payload["error"]["code"])

    def test_admin_can_update_member_role_and_scope_or_tenant_boundaries_apply(self) -> None:
        admin = self.application.auth.create_local_account(
            "tenant-role-admin", tenant_id="tenant-role", role="admin"
        )
        member = self.application.auth.create_local_account(
            "tenant-role-member", tenant_id="tenant-role", role="member"
        )
        other = self.application.auth.create_local_account(
            "tenant-role-other", tenant_id="tenant-other", role="member"
        )
        admin_session = self.application.auth.issue_account_session(admin["user_id"])
        read_only = self.application.auth.issue_account_session(
            admin["user_id"], scopes=["owner:read"]
        )
        member_session = self.application.auth.issue_account_session(member["user_id"])

        status, payload = self.request(
            "PATCH",
            f"/api/v1/tenant/members/{member['user_id']}",
            {"role": "admin"},
            token=admin_session["access_token"],
        )
        self.assertEqual(200, status)
        self.assertEqual("admin", payload["role"])
        status, _ = self.request(
            "GET", "/api/v1/personas", token=member_session["access_token"]
        )
        self.assertEqual(200, status)

        status, payload = self.request(
            "PATCH",
            f"/api/v1/tenant/members/{member['user_id']}",
            {"role": "member"},
            token=read_only["access_token"],
        )
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", payload["error"]["code"])

        status, payload = self.request(
            "PATCH",
            f"/api/v1/tenant/members/{other['user_id']}",
            {"role": "admin"},
            token=admin_session["access_token"],
        )
        self.assertEqual(404, status)
        self.assertEqual("tenant_member_not_found", payload["error"]["code"])

        status, payload = self.request(
            "PATCH",
            f"/api/v1/tenant/members/{member['user_id']}",
            {"role": "owner"},
            token=admin_session["access_token"],
        )
        self.assertEqual(400, status)
        self.assertEqual("tenant_role_invalid", payload["error"]["code"])


if __name__ == "__main__":
    unittest.main()
