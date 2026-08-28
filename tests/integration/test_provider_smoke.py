import base64
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
        self.__class__.request_body = json.loads(self.rfile.read(length).decode("utf-8"))
        payload = json.dumps(self.response_body, ensure_ascii=False).encode("utf-8")
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


if __name__ == "__main__":
    unittest.main()
