from __future__ import annotations

import http.client
import json
from pathlib import Path
import shutil
import threading
import unittest
from uuid import uuid4

from src.providers.gateway import ProviderError
from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server
from src.services.media_analysis_service import MediaAnalysisError


class _FakeMediaAnalysisService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.error: Exception | None = None

    def analyze(self, owner_id: str, import_id: str, **payload):
        self.calls.append((owner_id, import_id, payload))
        if self.error is not None:
            raise self.error
        return {
            "import_id": import_id,
            "file_id": payload.get("file_id") or "legacy-file",
            "state": "uploaded",
            "provider_id": payload["provider_id"],
            "model_id": payload["model_id"],
            "media_category": payload["data_category"],
            "media_type": "audio/wav" if payload["data_category"] == "audio" else ("video/mp4" if payload["data_category"] == "video" else "image/png"),
            "description": "受控音频转写" if payload["data_category"] == "audio" else ("受控视频描述" if payload["data_category"] == "video" else "受控图片描述"),
            "usage": {"prompt_tokens": 3},
            "provider_transfer": True,
            "provider_request_id": "request-1",
        }


class HttpMediaAnalysisTests(unittest.TestCase):
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
        self.fake_service = _FakeMediaAnalysisService()
        self.application.media_analysis = self.fake_service
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

    def request(self, method: str, path: str, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = dict(headers or {})
        if self.auth_token and "Authorization" not in request_headers:
            request_headers["Authorization"] = f"Bearer {self.auth_token}"
        encoded = None
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

    def test_media_analysis_route_requires_owner_authentication(self) -> None:
        token = self.auth_token
        self.auth_token = None
        status, _, payload = self.request(
            "POST",
            "/api/v1/imports/import-1/media-analysis",
            {"provider_id": "openai"},
        )
        self.auth_token = token

        self.assertEqual(401, status)
        self.assertEqual("authentication_required", payload["error"]["code"])
        self.assertEqual([], self.fake_service.calls)

    def test_media_analysis_route_returns_only_normalized_result_fields(self) -> None:
        status, _, payload = self.request(
            "POST",
            "/api/v1/imports/import-1/media-analysis",
            {
                "consent_id": "consent-1",
                "provider_id": "openai",
                "model_id": "gpt-4.1-mini",
                "data_category": "image",
                "authorization_scope": "persona-image-analysis",
                "prompt": "描述图片",
                "file_id": "file-1",
            },
        )

        self.assertEqual(200, status)
        self.assertEqual("受控图片描述", payload["description"])
        self.assertTrue(payload["provider_transfer"])
        self.assertNotIn("media_path", payload)
        self.assertNotIn("raw_bytes", payload)
        self.assertEqual(self.application.auth.owner_id, self.fake_service.calls[0][0])
        self.assertEqual("import-1", self.fake_service.calls[0][1])
        self.assertEqual("file-1", self.fake_service.calls[0][2]["file_id"])

    def test_media_analysis_route_maps_consent_and_provider_failures(self) -> None:
        self.fake_service.error = MediaAnalysisError("consent_revoked", "consent is revoked")
        status, _, payload = self.request(
            "POST",
            "/api/v1/imports/import-1/media-analysis",
            {"consent_id": "consent-1", "provider_id": "openai", "model_id": "gpt-4.1-mini", "data_category": "image", "authorization_scope": "scope", "prompt": "描述"},
        )
        self.assertEqual(428, status)
        self.assertEqual("consent_revoked", payload["error"]["code"])

        self.fake_service.error = ProviderError("provider_unavailable", "provider could not be reached")
        status, _, payload = self.request(
            "POST",
            "/api/v1/imports/import-1/media-analysis",
            {"consent_id": "consent-1", "provider_id": "openai", "model_id": "gpt-4.1-mini", "data_category": "image", "authorization_scope": "scope", "prompt": "描述"},
        )
        self.assertEqual(502, status)
        self.assertEqual("provider_unavailable", payload["error"]["code"])

    def test_media_analysis_route_exposes_normalized_audio_transcription(self) -> None:
        status, _, payload = self.request(
            "POST",
            "/api/v1/imports/import-1/media-analysis",
            {
                "consent_id": "consent-audio",
                "provider_id": "custom_openai",
                "model_id": "audio-model",
                "data_category": "audio",
                "authorization_scope": "persona-audio-transcription",
                "prompt": "请转写音频",
                "file_id": "file-1",
            },
        )

        self.assertEqual(200, status)
        self.assertEqual("audio", payload["media_category"])
        self.assertEqual("受控音频转写", payload["description"])
        self.assertEqual("audio/wav", payload["media_type"])
        self.assertNotIn("raw_bytes", payload)
        self.assertNotIn("media_path", payload)

    def test_media_analysis_route_exposes_normalized_video_description(self) -> None:
        status, _, payload = self.request(
            "POST",
            "/api/v1/imports/import-1/media-analysis",
            {
                "consent_id": "consent-video",
                "provider_id": "custom_openai",
                "model_id": "video-model",
                "data_category": "video",
                "authorization_scope": "persona-video-analysis",
                "prompt": "请概括视频",
                "file_id": "file-1",
            },
        )

        self.assertEqual(200, status)
        self.assertEqual("video", payload["media_category"])
        self.assertEqual("受控视频描述", payload["description"])
        self.assertEqual("video/mp4", payload["media_type"])
        self.assertNotIn("raw_bytes", payload)
        self.assertNotIn("media_path", payload)


if __name__ == "__main__":
    unittest.main()
