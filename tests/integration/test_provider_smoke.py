import base64
from email.parser import BytesParser
from email.policy import default
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from src.providers.base import ChatMessage, ChatRequest, MediaAnalysisRequest
from src.providers.catalog import ProviderCatalog
from src.providers.configuration import build_openai_compatible_adapters
from src.providers.gateway import ProviderGateway


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "provider_smoke.py"


class _OpenAICompatibleHandler(BaseHTTPRequestHandler):
    request_body: dict[str, object] | None = None
    request_authorization: str | None = None
    multipart_fields: dict[str, str] | None = None
    multipart_file: bytes | None = None
    multipart_file_content_type: str | None = None
    multipart_file_name: str | None = None
    request_path: str | None = None
    response_status = 200
    response_body = {
        "id": "chatcmpl-local-smoke",
        "choices": [
            {
                "message": {"content": "来自本地兼容端点的回复"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 4, "total_tokens": 6},
    }

    def do_POST(self):  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", "0"))
        self.__class__.request_authorization = self.headers.get("Authorization")
        self.__class__.request_path = self.path
        raw_body = self.rfile.read(length)
        if self.path.endswith("/audio/transcriptions") or self.path.endswith("/video/analyze"):
            envelope = (
                b"Content-Type: "
                + self.headers["Content-Type"].encode("ascii")
                + b"\r\nMIME-Version: 1.0\r\n\r\n"
                + raw_body
            )
            message = BytesParser(policy=default).parsebytes(envelope)
            fields: dict[str, str] = {}
            file_bytes = None
            for part in message.walk():
                if part.is_multipart():
                    continue
                name = part.get_param("name", header="content-disposition")
                if name == "file":
                    file_bytes = part.get_payload(decode=True)
                    self.__class__.multipart_file_content_type = part.get_content_type()
                    self.__class__.multipart_file_name = part.get_filename()
                elif isinstance(name, str):
                    value = part.get_payload(decode=True) or b""
                    fields[name] = value.decode("utf-8")
            self.__class__.multipart_fields = fields
            self.__class__.multipart_file = file_bytes
            if self.path.endswith("/video/analyze"):
                response_body = {"id": "video-local-smoke", "description": "来自本地视频端点的结果"}
            else:
                response_body = {"id": "audio-local-smoke", "text": "来自本地转写端点的结果"}
        else:
            self.__class__.request_body = json.loads(raw_body.decode("utf-8"))
            response_body = self.response_body
        payload = json.dumps(response_body, ensure_ascii=False).encode("utf-8")
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        return


class ProviderSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        _OpenAICompatibleHandler.request_body = None
        _OpenAICompatibleHandler.request_authorization = None
        _OpenAICompatibleHandler.multipart_fields = None
        _OpenAICompatibleHandler.multipart_file = None
        _OpenAICompatibleHandler.multipart_file_content_type = None
        _OpenAICompatibleHandler.multipart_file_name = None
        _OpenAICompatibleHandler.request_path = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAICompatibleHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_custom_openai_endpoint_runs_through_real_http_transport(self) -> None:
        port = self.server.server_address[1]
        environ = {
            "PAST_PARTNER_CUSTOM_OPENAI_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "PAST_PARTNER_CUSTOM_OPENAI_API_KEY": "smoke-secret",
            "PAST_PARTNER_CUSTOM_OPENAI_MODELS": "smoke-model",
        }
        base_catalog = ProviderCatalog.default()
        adapters = build_openai_compatible_adapters(base_catalog, environ)
        catalog = base_catalog.with_configured(
            set(adapters),
            {"custom_openai": frozenset({"smoke-model"})},
        )
        gateway = ProviderGateway(catalog, mode="development", adapters=adapters)

        response = gateway.chat(
            ChatRequest(
                provider_id="custom_openai",
                model_id="smoke-model",
                messages=(ChatMessage(role="user", content="请回复 smoke"),),
            )
        )

        self.assertEqual("来自本地兼容端点的回复", response.content)
        self.assertEqual("chatcmpl-local-smoke", response.provider_request_id)
        self.assertEqual("smoke-model", _OpenAICompatibleHandler.request_body["model"])
        self.assertEqual("Bearer smoke-secret", _OpenAICompatibleHandler.request_authorization)

    def test_keyless_custom_openai_endpoint_omits_authorization_header(self) -> None:
        port = self.server.server_address[1]
        environ = {
            "PAST_PARTNER_CUSTOM_OPENAI_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "PAST_PARTNER_CUSTOM_OPENAI_MODELS": "local-model",
        }
        base_catalog = ProviderCatalog.default()
        adapters = build_openai_compatible_adapters(base_catalog, environ)
        catalog = base_catalog.with_configured(
            set(adapters),
            {"custom_openai": frozenset({"local-model"})},
        )
        gateway = ProviderGateway(catalog, mode="development", adapters=adapters)

        response = gateway.chat(
            ChatRequest(
                provider_id="custom_openai",
                model_id="local-model",
                messages=(ChatMessage(role="user", content="请回复 local smoke"),),
            )
        )

        self.assertEqual("来自本地兼容端点的回复", response.content)
        self.assertIsNone(_OpenAICompatibleHandler.request_authorization)

    def test_provider_smoke_command_runs_custom_endpoint_end_to_end(self) -> None:
        port = self.server.server_address[1]
        environment = os.environ.copy()
        environment.update(
            {
                "PAST_PARTNER_PROVIDER_SMOKE": "1",
                "PAST_PARTNER_PROVIDER_SMOKE_PROVIDER": "custom_openai",
                "PAST_PARTNER_PROVIDER_SMOKE_MODEL": "smoke-model",
                "PAST_PARTNER_CUSTOM_OPENAI_BASE_URL": f"http://127.0.0.1:{port}/v1",
                "PAST_PARTNER_CUSTOM_OPENAI_API_KEY": "smoke-secret",
                "PAST_PARTNER_CUSTOM_OPENAI_MODELS": "smoke-model",
            }
        )
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--provider",
                "custom_openai",
                "--model",
                "smoke-model",
                "--prompt",
                "secret prompt",
            ],
            cwd=Path.cwd(),
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("ok", payload["status"])
        self.assertEqual("custom_openai", payload["provider"])
        self.assertNotIn("secret prompt", result.stdout)
        self.assertNotIn("secret response", result.stdout)
        self.assertNotIn("smoke-secret", result.stdout)
        self.assertEqual("smoke-model", _OpenAICompatibleHandler.request_body["model"])
        self.assertEqual("Bearer smoke-secret", _OpenAICompatibleHandler.request_authorization)

    def test_custom_openai_image_analysis_crosses_real_http_transport(self) -> None:
        port = self.server.server_address[1]
        environ = {
            "PAST_PARTNER_CUSTOM_OPENAI_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "PAST_PARTNER_CUSTOM_OPENAI_API_KEY": "smoke-secret",
            "PAST_PARTNER_CUSTOM_OPENAI_MODELS": "smoke-model",
        }
        base_catalog = ProviderCatalog.default()
        adapters = build_openai_compatible_adapters(base_catalog, environ)
        catalog = base_catalog.with_configured(
            set(adapters),
            {"custom_openai": frozenset({"smoke-model"})},
        )
        gateway = ProviderGateway(catalog, mode="development", adapters=adapters)
        media = b"fake-image"
        source_fd, source_name = tempfile.mkstemp(suffix=".png")
        os.close(source_fd)
        source_path = Path(source_name)
        source_path.write_bytes(media)
        self.addCleanup(lambda: source_path.unlink(missing_ok=True))
        response = gateway.analyze_media(
            MediaAnalysisRequest(
                provider_id="custom_openai",
                model_id="smoke-model",
                media_type="image/png",
                media_path=source_path,
                prompt="请描述 smoke 图片",
            )
        )

        self.assertEqual("来自本地兼容端点的回复", response.description)
        self.assertEqual("chatcmpl-local-smoke", response.provider_request_id)
        body = _OpenAICompatibleHandler.request_body
        self.assertEqual("smoke-model", body["model"])
        image = next(item for item in body["messages"][0]["content"] if item["type"] == "image_url")
        self.assertEqual(
            "data:image/png;base64," + base64.b64encode(media).decode("ascii"),
            image["image_url"]["url"],
        )
        self.assertEqual("Bearer smoke-secret", _OpenAICompatibleHandler.request_authorization)

    def test_custom_openai_audio_transcription_crosses_real_multipart_transport(self) -> None:
        port = self.server.server_address[1]
        environ = {
            "PAST_PARTNER_CUSTOM_OPENAI_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "PAST_PARTNER_CUSTOM_OPENAI_API_KEY": "smoke-secret",
            "PAST_PARTNER_CUSTOM_OPENAI_MODELS": "audio-model",
            "PAST_PARTNER_CUSTOM_OPENAI_AUDIO_MODELS": "audio-model",
        }
        base_catalog = ProviderCatalog.default()
        adapters = build_openai_compatible_adapters(base_catalog, environ)
        adapter = adapters["custom_openai"]
        catalog = base_catalog.with_configured(
            set(adapters),
            {"custom_openai": frozenset({"audio-model"})},
            media_capabilities={"custom_openai": adapter.config.media_capabilities},
        )
        gateway = ProviderGateway(catalog, mode="development", adapters=adapters)
        media = b"fake-audio"
        source_fd, source_name = tempfile.mkstemp(suffix=".bin")
        os.close(source_fd)
        source_path = Path(source_name)
        source_path.write_bytes(media)
        self.addCleanup(lambda: source_path.unlink(missing_ok=True))

        response = gateway.analyze_media(
            MediaAnalysisRequest(
                provider_id="custom_openai",
                model_id="audio-model",
                media_type="audio/wav",
                media_path=source_path,
                prompt="请转写 smoke 音频",
            )
        )

        self.assertEqual("来自本地转写端点的结果", response.description)
        self.assertEqual("audio-local-smoke", response.provider_request_id)
        self.assertEqual("/v1/audio/transcriptions", _OpenAICompatibleHandler.request_path)
        self.assertEqual(
            {"model": "audio-model", "prompt": "请转写 smoke 音频", "response_format": "json"},
            _OpenAICompatibleHandler.multipart_fields,
        )
        self.assertEqual(media, _OpenAICompatibleHandler.multipart_file)
        self.assertEqual("audio/wav", _OpenAICompatibleHandler.multipart_file_content_type)
        self.assertEqual("audio.wav", _OpenAICompatibleHandler.multipart_file_name)
        self.assertEqual("Bearer smoke-secret", _OpenAICompatibleHandler.request_authorization)

    def test_custom_openai_video_analysis_crosses_real_multipart_transport(self) -> None:
        port = self.server.server_address[1]
        environ = {
            "PAST_PARTNER_CUSTOM_OPENAI_BASE_URL": f"http://127.0.0.1:{port}/v1",
            "PAST_PARTNER_CUSTOM_OPENAI_API_KEY": "smoke-secret",
            "PAST_PARTNER_CUSTOM_OPENAI_MODELS": "video-model",
            "PAST_PARTNER_CUSTOM_OPENAI_VIDEO_MODELS": "video-model",
            "PAST_PARTNER_CUSTOM_OPENAI_VIDEO_ENDPOINT_PATH": "/video/analyze",
        }
        base_catalog = ProviderCatalog.default()
        adapters = build_openai_compatible_adapters(base_catalog, environ)
        adapter = adapters["custom_openai"]
        catalog = base_catalog.with_configured(
            set(adapters),
            {"custom_openai": frozenset({"video-model"})},
            media_capabilities={"custom_openai": adapter.config.media_capabilities},
        )
        gateway = ProviderGateway(catalog, mode="development", adapters=adapters)
        media = b"fake-video"
        source_fd, source_name = tempfile.mkstemp(suffix=".bin")
        os.close(source_fd)
        source_path = Path(source_name)
        source_path.write_bytes(media)
        self.addCleanup(lambda: source_path.unlink(missing_ok=True))

        response = gateway.analyze_media(
            MediaAnalysisRequest(
                provider_id="custom_openai",
                model_id="video-model",
                media_type="video/mp4",
                media_path=source_path,
                prompt="请概括 smoke 视频",
            )
        )

        self.assertEqual("来自本地视频端点的结果", response.description)
        self.assertEqual("video-local-smoke", response.provider_request_id)
        self.assertEqual("/v1/video/analyze", _OpenAICompatibleHandler.request_path)
        self.assertEqual(
            {"model": "video-model", "prompt": "请概括 smoke 视频", "response_format": "json"},
            _OpenAICompatibleHandler.multipart_fields,
        )
        self.assertEqual(media, _OpenAICompatibleHandler.multipart_file)
        self.assertEqual("video/mp4", _OpenAICompatibleHandler.multipart_file_content_type)
        self.assertEqual("video.mp4", _OpenAICompatibleHandler.multipart_file_name)
        self.assertEqual("Bearer smoke-secret", _OpenAICompatibleHandler.request_authorization)


if __name__ == "__main__":
    unittest.main()
