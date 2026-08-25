import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest

from src.providers.base import ChatMessage, ChatRequest
from src.providers.catalog import ProviderCatalog
from src.providers.configuration import build_openai_compatible_adapters
from src.providers.gateway import ProviderGateway


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


if __name__ == "__main__":
    unittest.main()
