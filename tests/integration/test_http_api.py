import hashlib
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


class HttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="development",
            cors_origins=("http://127.0.0.1:3000",),
        )
        self.server = create_server(config, Application.from_config(config))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.auth_token = None
        status, _, session = self.request("POST", "/api/v1/auth/session")
        self.assertEqual(201, status)
        self.auth_token = session["access_token"]
        self.owner_id = session["owner_id"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.data_root, ignore_errors=True)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        encoded = None
        request_headers = dict(headers or {})
        if self.auth_token and "Authorization" not in request_headers:
            request_headers["Authorization"] = f"Bearer {self.auth_token}"
        if isinstance(body, dict):
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif body is not None:
            encoded = body
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        response_headers = dict(response.getheaders())
        connection.close()
        decoded = json.loads(payload) if payload and "application/json" in response_headers.get("Content-Type", "") else payload
        return response.status, response_headers, decoded

    def test_health_persona_and_provider_catalog(self) -> None:
        status, _, health = self.request("GET", "/api/v1/health")
        self.assertEqual(200, status)
        self.assertEqual("healthy", health["status"])

        status, _, persona = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "妈妈", "relationship_type": "mother"},
        )
        self.assertEqual(201, status)
        self.assertEqual("mother", persona["relationship_type"])
        self.assertFalse((self.data_root / "personas").exists())
        database_bytes = (self.data_root / "database" / "past-partner.sqlite3").read_bytes()
        self.assertNotIn("妈妈".encode("utf-8"), database_bytes)

        status, _, providers = self.request("GET", "/api/v1/providers")
        self.assertEqual(200, status)
        self.assertIn("deepseek", {item["id"] for item in providers["providers"]})

    def test_persona_creation_accepts_full_relationship_schema(self) -> None:
        status, _, persona = self.request(
            "POST",
            "/api/v1/personas",
            {
                "display_name": "小雨",
                "relationship_type": "friend",
                "preferred_address": "你",
                "user_address": "小雨",
                "relationship_description": "大学同学",
                "tone_boundaries": ["温和", "不说教"],
                "forbidden_topics": ["家庭隐私"],
            },
        )

        self.assertEqual(201, status)
        self.assertEqual(1, persona["schema_version"])
        self.assertEqual("你", persona["preferred_address"])
        self.assertEqual(["温和", "不说教"], persona["tone_boundaries"])
        self.assertEqual(["家庭隐私"], persona["forbidden_topics"])

    def test_persona_can_be_read_and_partially_updated(self) -> None:
        _, _, created = self.request(
            "POST",
            "/api/v1/personas",
            {
                "display_name": "小雨",
                "relationship_type": "friend",
                "preferred_address": "你",
                "forbidden_topics": ["家庭隐私"],
            },
        )

        status, _, loaded = self.request("GET", f"/api/v1/personas/{created['id']}")
        self.assertEqual(200, status)
        self.assertEqual("你", loaded["preferred_address"])

        status, _, updated = self.request(
            "PATCH",
            f"/api/v1/personas/{created['id']}",
            {
                "display_name": "小雨同学",
                "preferred_address": None,
                "relationship_description": "大学同学",
            },
        )
        self.assertEqual(200, status)
        self.assertEqual("小雨同学", updated["display_name"])
        self.assertIsNone(updated["preferred_address"])
        self.assertEqual(["家庭隐私"], updated["forbidden_topics"])

        status, _, missing = self.request("GET", "/api/v1/personas/not-found")
        self.assertEqual(404, status)
        self.assertEqual("not_found", missing["error"]["code"])

    def test_persona_update_rejects_unknown_fields(self) -> None:
        _, _, created = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "小雨", "relationship_type": "friend"},
        )

        status, _, payload = self.request(
            "PATCH",
            f"/api/v1/personas/{created['id']}",
            {"owner_id": "attacker"},
        )

        self.assertEqual(400, status)
        self.assertEqual("invalid_persona", payload["error"]["code"])

    def test_data_routes_require_a_valid_owner_session(self) -> None:
        status, _, payload = self.request(
            "GET", "/api/v1/personas", headers={"Authorization": "Bearer invalid"}
        )
        self.assertEqual(401, status)
        self.assertEqual("authentication_required", payload["error"]["code"])

        status, _, health = self.request(
            "GET", "/api/v1/health", headers={"Authorization": "Bearer invalid"}
        )
        self.assertEqual(200, status)
        self.assertEqual("healthy", health["status"])

    def test_import_chunk_status_and_complete_flow(self) -> None:
        _, _, persona = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "小雨", "relationship_type": "friend"},
        )
        content = b"hello"
        status, _, job = self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": persona["id"],
                "source_name": "chat.txt",
                "total_bytes": len(content),
                "media_type": "text/plain",
            },
        )
        self.assertEqual(201, status)

        digest = hashlib.sha256(content).hexdigest()
        status, _, receipt = self.request(
            "PUT",
            f"/api/v1/imports/{job['id']}/chunks/0",
            content,
            {"Content-Length": str(len(content)), "X-Chunk-Sha256": digest},
        )
        self.assertEqual(200, status)
        self.assertEqual(len(content), receipt["received_bytes"])

        status, _, stored = self.request("GET", f"/api/v1/imports/{job['id']}")
        self.assertEqual(200, status)
        self.assertEqual("uploading", stored["state"])
        self.assertFalse((self.data_root / "imports").exists())
        self.assertFalse((self.data_root / "upload-manifests").exists())
        database_bytes = (self.data_root / "database" / "past-partner.sqlite3").read_bytes()
        self.assertNotIn(b"chat.txt", database_bytes)

        status, _, completed = self.request(
            "POST",
            f"/api/v1/imports/{job['id']}/complete",
            {"sha256": digest},
        )
        self.assertEqual(200, status)
        self.assertEqual("uploaded", completed["state"])
        encrypted_payload = self.server.application.uploads.payload_path(job["id"]).read_bytes()
        self.assertNotIn(content, encrypted_payload)
        self.assertEqual(
            content,
            b"".join(self.server.application.uploads.iter_payload(self.owner_id, job["id"])),
        )

    def test_missing_chunks_endpoint_returns_resume_status(self) -> None:
        _, _, persona = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "小雨", "relationship_type": "friend"},
        )
        _, _, job = self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": persona["id"],
                "source_name": "chat.txt",
                "total_bytes": 11,
                "media_type": "text/plain",
            },
        )

        for index, content in ((0, b"hello"), (2, b"ok")):
            digest = hashlib.sha256(content).hexdigest()
            status, _, _ = self.request(
                "PUT",
                f"/api/v1/imports/{job['id']}/chunks/{index}",
                content,
                {"Content-Length": str(len(content)), "X-Chunk-Sha256": digest},
            )
            self.assertEqual(200, status)

        status, _, payload = self.request(
            "GET",
            f"/api/v1/imports/{job['id']}/missing-chunks?expected_chunks=3",
        )

        self.assertEqual(200, status)
        self.assertEqual("uploading", payload["state"])
        self.assertEqual(3, payload["expected_chunk_count"])
        self.assertEqual([0, 2], payload["received_chunks"])
        self.assertEqual([1], payload["missing_chunks"])
        self.assertEqual(7, payload["received_bytes"])

    def test_missing_chunks_endpoint_validates_expected_count(self) -> None:
        _, _, persona = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "小雨", "relationship_type": "friend"},
        )
        _, _, job = self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": persona["id"],
                "source_name": "chat.txt",
                "total_bytes": 5,
                "media_type": "text/plain",
            },
        )
        content = b"hello"
        self.request(
            "PUT",
            f"/api/v1/imports/{job['id']}/chunks/2",
            content,
            {"Content-Length": str(len(content)), "X-Chunk-Sha256": hashlib.sha256(content).hexdigest()},
        )

        status, _, payload = self.request(
            "GET",
            f"/api/v1/imports/{job['id']}/missing-chunks?expected_chunks=2",
        )

        self.assertEqual(400, status)
        self.assertEqual("invalid_expected_chunk_count", payload["error"]["code"])

    def test_import_creation_accepts_multiple_files_and_returns_file_ids(self) -> None:
        _, _, persona = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "小雨", "relationship_type": "friend"},
        )

        status, _, job = self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": persona["id"],
                "files": [
                    {"source_name": "chat.txt", "media_type": "text/plain", "total_bytes": 5},
                    {"source_name": "photo.jpg", "media_type": "image/jpeg", "total_bytes": 7},
                ],
            },
        )

        self.assertEqual(201, status)
        self.assertEqual(12, job["total_bytes"])
        self.assertEqual(2, len(job["files"]))
        self.assertTrue(all(item["file_id"] for item in job["files"]))

        status, _, loaded = self.request("GET", f"/api/v1/imports/{job['id']}")
        self.assertEqual(200, status)
        self.assertEqual(job["files"], loaded["files"])

    def test_import_creation_rejects_multi_file_aggregate_over_three_gib(self) -> None:
        _, _, persona = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "小雨", "relationship_type": "friend"},
        )

        status, _, payload = self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": persona["id"],
                "files": [
                    {"source_name": "wechat.db", "media_type": "application/octet-stream", "total_bytes": 3 * 1024**3},
                    {"source_name": "photos.zip", "media_type": "application/zip", "total_bytes": 1},
                ],
            },
        )

        self.assertEqual(400, status)
        self.assertEqual("import_too_large", payload["error"]["code"])

    def test_rejected_chunk_closes_connection_when_body_may_be_unread(self) -> None:
        _, _, persona = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "小雨", "relationship_type": "friend"},
        )
        original = b"hello"
        _, _, job = self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": persona["id"],
                "source_name": "chat.txt",
                "total_bytes": len(original),
                "media_type": "text/plain",
            },
        )
        self.request(
            "PUT",
            f"/api/v1/imports/{job['id']}/chunks/0",
            original,
            {"X-Chunk-Sha256": hashlib.sha256(original).hexdigest()},
        )

        conflicting = b"world"
        status, headers, payload = self.request(
            "PUT",
            f"/api/v1/imports/{job['id']}/chunks/0",
            conflicting,
            {"X-Chunk-Sha256": hashlib.sha256(conflicting).hexdigest()},
        )

        self.assertEqual(409, status)
        self.assertEqual("chunk_conflict", payload["error"]["code"])
        self.assertEqual("close", headers.get("Connection"))

    def test_requires_content_length_and_returns_structured_errors(self) -> None:
        status, _, payload = self.request("POST", "/api/v1/imports", {"total_bytes": 1})
        self.assertEqual(400, status)
        self.assertIn("code", payload["error"])

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.putrequest("PUT", "/api/v1/imports/not-real/chunks/0")
        connection.putheader("Authorization", f"Bearer {self.auth_token}")
        connection.putheader("X-Chunk-Sha256", "0" * 64)
        connection.endheaders()
        response = connection.getresponse()
        response.read()
        self.assertEqual(411, response.status)
        connection.close()

    def test_unconfigured_chat_returns_service_unavailable(self) -> None:
        status, _, payload = self.request(
            "POST",
            "/api/v1/chat",
            {
                "provider_id": "deepseek",
                "model_id": "deepseek-v4-flash",
                "messages": [{"role": "user", "content": "你好"}],
            },
        )
        self.assertEqual(503, status)
        self.assertEqual("provider_not_configured", payload["error"]["code"])

    def test_cors_preflight_only_echoes_allowed_origin(self) -> None:
        status, headers, _ = self.request(
            "OPTIONS",
            "/api/v1/personas",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(204, status)
        self.assertEqual("http://127.0.0.1:3000", headers["Access-Control-Allow-Origin"])

        self.assertIn("PATCH", headers["Access-Control-Allow-Methods"])


if __name__ == "__main__":
    unittest.main()
