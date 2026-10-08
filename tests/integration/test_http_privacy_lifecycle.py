import hashlib
import http.client
import io
import json
import shutil
import threading
import unittest
import zipfile
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server
from src.services.import_service import ImportState
from src.services.upload_service import UploadError
from src.services.local_auth import LocalAuthError
from src.services.oidc_verifier import OidcClaims


class HttpPrivacyLifecycleTests(unittest.TestCase):
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
        self.auth_token = self._session()["access_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.application.close()
        shutil.rmtree(self.data_root, ignore_errors=True)

    def _session(self) -> dict:
        status, _, payload = self.request("POST", "/api/v1/auth/session", auth=False)
        self.assertEqual(201, status)
        return payload

    def request(self, method, path, body=None, *, auth=True, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = dict(headers or {})
        if auth and getattr(self, "auth_token", None):
            request_headers.setdefault("Authorization", f"Bearer {self.auth_token}")
        encoded = None
        if isinstance(body, dict):
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif body is not None:
            encoded = body
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        result_headers = dict(response.getheaders())
        connection.close()
        if "application/json" in result_headers.get("Content-Type", ""):
            payload = json.loads(payload)
        return response.status, result_headers, payload

    def _create_completed_import(self) -> tuple[dict, bytes]:
        status, _, persona = self.request(
            "POST", "/api/v1/personas", {"display_name": "归档人物", "relationship_type": "friend"}
        )
        self.assertEqual(201, status)
        raw = "2026-08-24 10:00:00\n我: 你好\n".encode("utf-8")
        status, _, job = self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": persona["id"],
                "source_name": "chat.txt",
                "total_bytes": len(raw),
                "media_type": "text/plain",
            },
        )
        self.assertEqual(201, status)
        status, _, _ = self.request(
            "PUT",
            f"/api/v1/imports/{job['id']}/chunks/0",
            raw,
            headers={
                "Content-Length": str(len(raw)),
                "X-Chunk-Sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        self.assertEqual(200, status)
        status, _, completed = self.request("POST", f"/api/v1/imports/{job['id']}/complete", {})
        self.assertEqual(200, status)
        self.assertEqual("uploaded", completed["state"])
        status, _, preview = self.request("GET", f"/api/v1/imports/{job['id']}/preview")
        self.assertEqual(200, status)
        self.assertGreaterEqual(preview["summary"]["record_count"], 1)
        return persona, raw

    def test_archive_export_contains_complete_raw_payload(self) -> None:
        persona, raw = self._create_completed_import()

        status, headers, payload = self.request("GET", "/api/v1/data-export/archive")

        self.assertEqual(200, status)
        self.assertEqual("application/zip", headers["Content-Type"])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertIn("manifest.json", archive.namelist())
            self.assertEqual(raw, archive.read(next(name for name in archive.namelist() if name.endswith("/payload.bin"))))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual([persona["id"]], [item["id"] for item in manifest["personas"]])
            self.assertTrue(manifest["scope"]["raw_payloads_included"])
            self.assertEqual(1, manifest["archive"]["raw_object_count"])
            self.assertEqual(len(raw), manifest["archive"]["raw_bytes"])
            self.assertIn("provider_side_data", manifest["scope"]["omitted"])

    def test_empty_archive_declares_zero_raw_payloads(self) -> None:
        status, headers, payload = self.request("GET", "/api/v1/data-export/archive")

        self.assertEqual(200, status)
        self.assertEqual("application/zip", headers["Content-Type"])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            manifest = json.loads(archive.read("manifest.json"))

        self.assertEqual([], manifest["personas"])
        self.assertEqual([], manifest["imports"])
        self.assertEqual(0, manifest["archive"]["raw_object_count"])
        self.assertEqual(0, manifest["archive"]["raw_bytes"])
        self.assertEqual(["provider_side_data", "audit_records"], manifest["scope"]["omitted"])

    def test_repeated_preview_does_not_refresh_normalized_retention_anchor(self) -> None:
        self._create_completed_import()
        status, _, first = self.request("GET", "/api/v1/imports")
        self.assertEqual(200, status)
        first_anchor = first["imports"][0]["normalized_at"]

        status, _, _ = self.request("GET", f"/api/v1/imports/{first['imports'][0]['id']}/preview")
        self.assertEqual(200, status)
        status, _, second = self.request("GET", "/api/v1/imports")
        self.assertEqual(200, status)
        self.assertEqual(first_anchor, second["imports"][0]["normalized_at"])

    def test_owner_deletion_requires_confirmation_and_removes_service_data(self) -> None:
        self._create_completed_import()

        status, _, payload = self.request("POST", "/api/v1/data-deletion", {"confirm": "no"})
        self.assertEqual(400, status)
        self.assertEqual("deletion_confirmation_required", payload["error"]["code"])

        status, _, deleted = self.request("POST", "/api/v1/data-deletion", {"confirm": "DELETE"})
        self.assertEqual(200, status)
        self.assertTrue(deleted["deleted"])
        self.assertTrue(deleted["receipt_id"])
        self.assertGreaterEqual(deleted["deleted_imports"], 1)
        receipt = self.application.deletion_receipts.get(deleted["receipt_id"])
        self.assertIsNotNone(receipt)
        self.assertEqual(deleted["deleted_imports"], receipt["counts"]["imports"])
        self.assertNotIn("owner_id", receipt["counts"])
        self.assertNotIn("path", receipt["counts"])
        self.assertEqual({"receipt_id", "deleted_at", "counts"}, set(receipt))
        encoded_receipt = json.dumps(receipt, ensure_ascii=False)
        for forbidden in ("token", "provider_key", "provider_api_key", "content", "body"):
            self.assertNotIn(forbidden, encoded_receipt)

        self.auth_token = self._session()["access_token"]
        status, _, personas = self.request("GET", "/api/v1/personas")
        self.assertEqual(200, status)
        self.assertEqual([], personas["personas"])
        status, _, imports = self.request("GET", "/api/v1/imports")
        self.assertEqual(200, status)
        self.assertEqual([], imports["imports"])

    def test_owner_deletion_rejects_processing_import_without_side_effects(self) -> None:
        status, _, persona = self.request(
            "POST", "/api/v1/personas", {"display_name": "处理中人物", "relationship_type": "friend"}
        )
        self.assertEqual(201, status)
        status, _, created = self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": persona["id"],
                "source_name": "processing.txt",
                "total_bytes": 1,
                "media_type": "text/plain",
            },
        )
        self.assertEqual(201, status)
        owner_id = self.application.auth.owner_id
        processing = replace(
            self.application.imports.get(owner_id, created["id"]),
            state=ImportState.PROCESSING,
        )
        self.application.imports.save(owner_id, processing)

        status, _, payload = self.request("POST", "/api/v1/data-deletion", {"confirm": "DELETE"})

        self.assertEqual(409, status)
        self.assertEqual("deletion_unavailable", payload["error"]["code"])
        status, _, personas = self.request("GET", "/api/v1/personas")
        self.assertEqual(200, status)
        self.assertEqual([persona["id"]], [item["id"] for item in personas["personas"]])
        status, _, imports = self.request("GET", "/api/v1/imports")
        self.assertEqual(200, status)
        self.assertEqual([created["id"]], [item["id"] for item in imports["imports"]])

    def test_owner_deletion_object_failure_is_stable_and_has_no_success_receipt(self) -> None:
        self._create_completed_import()

        with patch.object(
            self.application.uploads,
            "delete_import",
            side_effect=UploadError("deletion_failed", "object cleanup failed"),
        ):
            status, _, payload = self.request("POST", "/api/v1/data-deletion", {"confirm": "DELETE"})

        self.assertEqual(500, status)
        self.assertEqual("deletion_failed", payload["error"]["code"])
        self.assertNotIn("receipt_id", payload)
        status, _, imports = self.request("GET", "/api/v1/imports")
        self.assertEqual(200, status)
        self.assertEqual(1, len(imports["imports"]))

    def test_account_erasure_clears_old_notices_and_preserves_other_accounts(self) -> None:
        auth = self.application.auth
        target = auth.issue_oidc_session(
            OidcClaims("https://issuer.example", "erase-target", "past-partner",
                       "shared-tenant", datetime.now(UTC) + timedelta(minutes=5)),
            remote_address="127.0.0.1",
        )
        others = []
        for subject, tenant in (("erase-peer", "shared-tenant"), ("erase-other", "other-tenant")):
            session = auth.issue_oidc_session(
                OidcClaims("https://issuer.example", subject, "past-partner", tenant,
                           datetime.now(UTC) + timedelta(minutes=5)),
                remote_address="127.0.0.1",
            )
            self.auth_token = session["access_token"]
            persona, raw = self._create_completed_import()
            self.assertEqual(200, self.request("GET", "/api/v1/data-export")[0])
            notices = self.request("GET", "/api/v1/notifications")[2]["notifications"]
            others.append((session, persona, raw, notices))

        self.auth_token = target["access_token"]
        self._create_completed_import()
        for _ in range(2):
            self.assertEqual(200, self.request("GET", "/api/v1/data-export")[0])
        self.assertEqual(2, len(self.request("GET", "/api/v1/notifications")[2]["notifications"]))
        # A request body cannot select another account; the authenticated subject
        # remains the sole resource boundary even for users in the same tenant.
        status, _, deleted = self.request(
            "POST", "/api/v1/data-deletion",
            {"confirm": "DELETE", "owner_id": others[0][0]["user_id"]},
        )
        self.assertEqual(200, status)
        self.assertEqual(401, self.request("GET", "/api/v1/personas")[0])
        with self.assertRaises(LocalAuthError):
            auth.refresh_oidc_session(target["refresh_token"], remote_address="127.0.0.1")

        # Data deletion retains the technical identity. Reauthenticate to inspect
        # its cleared resources and the single new, bounded deletion notification.
        self.auth_token = auth.issue_account_session(target["user_id"])["access_token"]
        self.assertEqual([], self.request("GET", "/api/v1/personas")[2]["personas"])
        self.assertEqual([], self.request("GET", "/api/v1/imports")[2]["imports"])
        notices = self.request("GET", "/api/v1/notifications")[2]["notifications"]
        self.assertEqual(1, len(notices))
        self.assertEqual("deletion_completed", notices[0]["event_type"])
        self.assertEqual(deleted["receipt_id"], notices[0]["operation_id"])
        self.assertEqual(2, notices[0]["counts"]["notifications"])
        receipt = self.application.deletion_receipts.get(deleted["receipt_id"])
        self.assertEqual(2, receipt["counts"]["notifications"])
        for secret in (target["user_id"], target["access_token"], target["refresh_token"], "erase-target"):
            self.assertNotIn(secret, json.dumps(receipt))

        for session, persona, raw, original_notices in others:
            with self.subTest(account=session["user_id"]):
                self.auth_token = session["access_token"]
                self.assertEqual(200, self.request("GET", f"/api/v1/personas/{persona['id']}")[0])
                self.assertEqual(original_notices, self.request("GET", "/api/v1/notifications")[2]["notifications"])
                status, _, payload = self.request("GET", "/api/v1/data-export/archive")
                self.assertEqual(200, status)
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    payload_name = next(name for name in archive.namelist() if name.endswith("/payload.bin"))
                    self.assertEqual(raw, archive.read(payload_name))
                refreshed = auth.refresh_oidc_session(session["refresh_token"], remote_address="127.0.0.1")
                self.assertEqual(session["user_id"], refreshed["user_id"])
                self.assertEqual(session["user_id"], auth.authenticate(f"Bearer {refreshed['access_token']}").user_id)

    def test_erasure_rolls_back_old_notices_and_sessions_if_new_notice_fails(self) -> None:
        target = self.application.auth.issue_oidc_session(
            OidcClaims("https://issuer.example", "erase-rollback", "past-partner", "rollback-tenant",
                       datetime.now(UTC) + timedelta(minutes=5)),
            remote_address="127.0.0.1",
        )
        self.auth_token = target["access_token"]
        status, _, persona = self.request(
            "POST", "/api/v1/personas", {"display_name": "Rollback", "relationship_type": "friend"}
        )
        self.assertEqual(201, status)
        self.request("GET", "/api/v1/data-export")
        original_notices = self.request("GET", "/api/v1/notifications")[2]["notifications"]
        with patch.object(self.application.notifications, "record_deletion", side_effect=RuntimeError("notice failed")):
            with self.assertRaisesRegex(RuntimeError, "notice failed"):
                self.application.delete_owner_data(target["user_id"], {"confirm": "DELETE"})
        self.assertEqual(original_notices, self.request("GET", "/api/v1/notifications")[2]["notifications"])
        self.assertEqual(200, self.request("GET", f"/api/v1/personas/{persona['id']}")[0])
        refreshed = self.application.auth.refresh_oidc_session(
            target["refresh_token"], remote_address="127.0.0.1"
        )
        self.assertEqual(target["user_id"], refreshed["user_id"])
        with self.application.metadata_store.transaction() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM deletion_receipts").fetchone()[0])

    def test_repeated_erasure_replaces_previous_deletion_notice(self) -> None:
        self.request("GET", "/api/v1/data-export")
        first = self.request("POST", "/api/v1/data-deletion", {"confirm": "DELETE"})[2]
        self.auth_token = self._session()["access_token"]
        status, _, second = self.request("POST", "/api/v1/data-deletion", {"confirm": "DELETE"})
        self.assertEqual(200, status)
        self.assertNotEqual(first["receipt_id"], second["receipt_id"])
        self.auth_token = self._session()["access_token"]
        notices = self.request("GET", "/api/v1/notifications")[2]["notifications"]
        self.assertEqual(1, len(notices))
        self.assertEqual(second["receipt_id"], notices[0]["operation_id"])
        self.assertEqual(1, notices[0]["counts"]["notifications"])

    def test_member_erasure_requires_confirmation_and_write_scope(self) -> None:
        auth = self.application.auth
        member = auth.create_local_account("erasure-scope", tenant_id="erasure-scope")
        self.auth_token = auth.issue_account_session(member["user_id"])["access_token"]
        status, _, persona = self.request(
            "POST", "/api/v1/personas", {"display_name": "Keep", "relationship_type": "friend"}
        )
        self.assertEqual(201, status)
        self.request("GET", "/api/v1/data-export")
        notices = self.request("GET", "/api/v1/notifications")[2]["notifications"]
        status, _, result = self.request("POST", "/api/v1/data-deletion", {"confirm": "no"})
        self.assertEqual(400, status)
        self.assertEqual("deletion_confirmation_required", result["error"]["code"])
        self.auth_token = auth.issue_account_session(member["user_id"], scopes=["owner:read"])["access_token"]
        status, _, result = self.request("POST", "/api/v1/data-deletion", {"confirm": "DELETE"})
        self.assertEqual(403, status)
        self.assertEqual("insufficient_scope", result["error"]["code"])
        self.assertEqual(200, self.request("GET", f"/api/v1/personas/{persona['id']}")[0])
        self.assertEqual(notices, self.request("GET", "/api/v1/notifications")[2]["notifications"])


if __name__ == "__main__":
    unittest.main()
