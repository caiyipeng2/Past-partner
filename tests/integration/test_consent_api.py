"""P1-10 consent API contracts."""

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


class ConsentApiTests(unittest.TestCase):
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
        self.auth_token = None
        status, _, session = self.request("POST", "/api/v1/auth/session")
        self.assertEqual(201, status)
        self.auth_token = session["access_token"]

        status, _, persona = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "小雨", "relationship_type": "friend"},
        )
        self.assertEqual(201, status)
        self.persona_id = persona["id"]

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

    def test_creates_lists_and_revokes_media_consent(self) -> None:
        status, _, created = self.request(
            "POST",
            "/api/v1/consents",
            {
                "persona_id": self.persona_id,
                "provider_id": "deepseek",
                "model_id": "deepseek-chat",
                "data_category": "image",
                "estimated_cost": 0.12,
                "purpose": "生成图片描述",
                "authorization_scope": "persona-image-analysis",
            },
        )
        self.assertEqual(201, status)
        self.assertEqual("active", created["status"])

        status, _, listed = self.request(
            "GET",
            f"/api/v1/consents?persona_id={self.persona_id}",
        )
        self.assertEqual(200, status)
        self.assertEqual([created["id"]], [item["id"] for item in listed["consents"]])

        status, _, revoked = self.request(
            "POST",
            f"/api/v1/consents/{created['id']}/revoke",
            {},
        )
        self.assertEqual(200, status)
        self.assertEqual("revoked", revoked["status"])
        self.assertIsNotNone(revoked["revoked_at"])

    def test_persona_deletion_removes_consent_records(self) -> None:
        status, _, created = self.request(
            "POST",
            "/api/v1/consents",
            {
                "persona_id": self.persona_id,
                "provider_id": "qwen",
                "model_id": "qwen-plus",
                "data_category": "audio",
                "estimated_cost": 0,
                "purpose": "音频转写",
                "authorization_scope": "persona-audio-transcription",
            },
        )
        self.assertEqual(201, status)

        status, _, deleted = self.request("DELETE", f"/api/v1/personas/{self.persona_id}")
        self.assertEqual(200, status)
        self.assertEqual(1, deleted["deleted_consents"])

        status, _, listed = self.request(
            "GET",
            f"/api/v1/consents?persona_id={self.persona_id}",
        )
        self.assertEqual(200, status)
        self.assertEqual([], listed["consents"])

    def test_consent_creation_requires_scope_fields(self) -> None:
        status, _, payload = self.request(
            "POST",
            "/api/v1/consents",
            {"persona_id": self.persona_id, "provider_id": "deepseek"},
        )
        self.assertEqual(400, status)
        self.assertEqual("missing_field", payload["error"]["code"])

    def test_authorize_endpoint_enforces_provider_model_media_capability(self) -> None:
        status, _, vision_consent = self.request(
            "POST",
            "/api/v1/consents",
            {
                "persona_id": self.persona_id,
                "provider_id": "openai",
                "model_id": "gpt-4.1-mini",
                "data_category": "image",
                "estimated_cost": 0,
                "purpose": "图片理解",
                "authorization_scope": "persona-image-analysis",
            },
        )
        self.assertEqual(201, status)

        status, _, decision = self.request(
            "POST",
            f"/api/v1/consents/{vision_consent['id']}/authorize",
            {
                "provider_id": "openai",
                "model_id": "gpt-4.1-mini",
                "data_category": "image",
                "authorization_scope": "persona-image-analysis",
            },
        )
        self.assertEqual(200, status)
        self.assertTrue(decision["authorized"])
        self.assertEqual("vision", decision["required_capability"])

        status, _, unsupported_consent = self.request(
            "POST",
            "/api/v1/consents",
            {
                "persona_id": self.persona_id,
                "provider_id": "deepseek",
                "model_id": "deepseek-v4-flash",
                "data_category": "image",
                "estimated_cost": 0,
                "purpose": "图片理解",
                "authorization_scope": "persona-image-analysis",
            },
        )
        self.assertEqual(201, status)

        status, _, payload = self.request(
            "POST",
            f"/api/v1/consents/{unsupported_consent['id']}/authorize",
            {
                "provider_id": "deepseek",
                "model_id": "deepseek-v4-flash",
                "data_category": "image",
                "authorization_scope": "persona-image-analysis",
            },
        )
        self.assertEqual(422, status)
        self.assertEqual("model_capability_missing", payload["error"]["code"])

    def test_ocr_consent_endpoint_creates_fixed_image_ocr_scope(self) -> None:
        self._enable_ocr_model()

        status, _, created = self.request(
            "POST",
            "/api/v1/consents/ocr",
            {
                "persona_id": self.persona_id,
                "provider_id": "custom_openai",
                "model_id": "ocr-model",
                "estimated_cost": 0.03,
            },
        )

        self.assertEqual(201, status)
        self.assertEqual("image", created["data_category"])
        self.assertEqual("image_ocr", created["purpose"])
        self.assertEqual("persona-image-ocr", created["authorization_scope"])
        self.assertEqual("active", created["status"])

    def test_ocr_consent_endpoint_rejects_model_without_ocr_capability(self) -> None:
        status, _, payload = self.request(
            "POST",
            "/api/v1/consents/ocr",
            {
                "persona_id": self.persona_id,
                "provider_id": "openai",
                "model_id": "gpt-4.1-mini",
                "estimated_cost": 0,
            },
        )

        self.assertEqual(422, status)
        self.assertEqual("model_capability_missing", payload["error"]["code"])

    def test_ocr_consent_endpoint_requires_owner_authentication(self) -> None:
        token = self.auth_token
        self.auth_token = None
        status, _, payload = self.request(
            "POST",
            "/api/v1/consents/ocr",
            {
                "persona_id": self.persona_id,
                "provider_id": "custom_openai",
                "model_id": "ocr-model",
                "estimated_cost": 0,
            },
        )
        self.auth_token = token

        self.assertEqual(401, status)
        self.assertEqual("authentication_required", payload["error"]["code"])

    def test_ocr_consent_endpoint_rejects_attempt_to_override_fixed_scope(self) -> None:
        self._enable_ocr_model()

        status, _, payload = self.request(
            "POST",
            "/api/v1/consents/ocr",
            {
                "persona_id": self.persona_id,
                "provider_id": "custom_openai",
                "model_id": "ocr-model",
                "estimated_cost": 0,
                "purpose": "普通图片理解",
            },
        )

        self.assertEqual(400, status)
        self.assertEqual("unsupported_field", payload["error"]["code"])

    def test_ocr_consent_endpoint_rejects_non_string_model_id(self) -> None:
        status, _, payload = self.request(
            "POST",
            "/api/v1/consents/ocr",
            {
                "persona_id": self.persona_id,
                "provider_id": "custom_openai",
                "model_id": [],
                "estimated_cost": 0,
            },
        )

        self.assertEqual(400, status)
        self.assertEqual("invalid_model", payload["error"]["code"])

    def test_ocr_consent_endpoint_rejects_duplicate_active_scope(self) -> None:
        self._enable_ocr_model()
        request_body = {
            "persona_id": self.persona_id,
            "provider_id": "custom_openai",
            "model_id": "ocr-model",
            "estimated_cost": 0,
        }
        status, _, _ = self.request("POST", "/api/v1/consents/ocr", request_body)
        self.assertEqual(201, status)

        status, _, payload = self.request("POST", "/api/v1/consents/ocr", request_body)

        self.assertEqual(409, status)
        self.assertEqual("consent_exists", payload["error"]["code"])

    def test_created_ocr_consent_authorizes_ocr_analysis(self) -> None:
        self._enable_ocr_model()
        status, _, created = self.request(
            "POST",
            "/api/v1/consents/ocr",
            {
                "persona_id": self.persona_id,
                "provider_id": "custom_openai",
                "model_id": "ocr-model",
                "estimated_cost": 0,
            },
        )
        self.assertEqual(201, status)

        status, _, decision = self.request(
            "POST",
            f"/api/v1/consents/{created['id']}/authorize",
            {
                "provider_id": "custom_openai",
                "model_id": "ocr-model",
                "data_category": "image",
                "authorization_scope": "persona-image-ocr",
                "analysis_kind": "ocr",
            },
        )

        self.assertEqual(200, status)
        self.assertTrue(decision["authorized"])
        self.assertEqual("ocr", decision["required_capability"])

    def _enable_ocr_model(self) -> None:
        from src.providers.catalog import ProviderCatalog

        catalog = ProviderCatalog.default().with_configured(
            {"custom_openai"},
            {"custom_openai": frozenset({"ocr-model"})},
            media_capabilities={"custom_openai": {"ocr-model": frozenset({"ocr"})}},
        )
        self.server.application.catalog = catalog
        self.server.application.multimodal_consents.catalog = catalog


if __name__ == "__main__":
    unittest.main()
