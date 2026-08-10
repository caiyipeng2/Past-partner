import unittest

from src.providers.base import AdapterError, ChatMessage, ChatRequest
from src.providers.catalog import ProviderCatalog
from src.providers.gateway import ProviderGateway
from src.providers.native import AnthropicAdapter, AnthropicConfig, GeminiAdapter, GeminiConfig


class NativeProviderAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = ChatRequest(
            provider_id="anthropic",
            model_id="claude-sonnet-4-5",
            messages=(
                ChatMessage(role="system", content="保持简洁"),
                ChatMessage(role="user", content="你好"),
                ChatMessage(role="assistant", content="你好，我在。"),
            ),
            temperature=0.2,
        )

    def test_anthropic_adapter_normalizes_messages_and_response(self) -> None:
        calls = []

        def transport(url, headers, body, timeout_seconds):
            calls.append((url, headers, body, timeout_seconds))
            return {
                "id": "msg_test",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "我在这里。"}],
                "usage": {"input_tokens": 7, "output_tokens": 4},
            }

        adapter = AnthropicAdapter(
            AnthropicConfig(
                provider_id="anthropic",
                base_url="https://api.anthropic.com",
                api_key="anthropic-secret",
                allowed_models=frozenset({"claude-sonnet-4-5"}),
            ),
            transport=transport,
        )

        response = adapter.chat(self.request)

        self.assertEqual("我在这里。", response.content)
        self.assertEqual({"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11}, response.usage)
        self.assertEqual("https://api.anthropic.com/v1/messages", calls[0][0])
        self.assertEqual("anthropic-secret", calls[0][1]["x-api-key"])
        self.assertEqual("2023-06-01", calls[0][1]["anthropic-version"])
        self.assertEqual("保持简洁", calls[0][2]["system"])
        self.assertEqual("user", calls[0][2]["messages"][0]["role"])
        self.assertEqual("assistant", calls[0][2]["messages"][1]["role"])
        self.assertEqual("claude-sonnet-4-5", calls[0][2]["model"])

    def test_gemini_adapter_maps_roles_and_usage(self) -> None:
        calls = []

        def transport(url, headers, body, timeout_seconds):
            calls.append((url, headers, body, timeout_seconds))
            return {
                "responseId": "response_test",
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": "你好，我在。"}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 6,
                    "candidatesTokenCount": 5,
                    "totalTokenCount": 11,
                },
            }

        adapter = GeminiAdapter(
            GeminiConfig(
                provider_id="gemini",
                base_url="https://generativelanguage.googleapis.com",
                api_key="gemini-secret",
                allowed_models=frozenset({"gemini-2.5-flash"}),
            ),
            transport=transport,
        )

        response = adapter.chat(
            ChatRequest(
                provider_id="gemini",
                model_id="gemini-2.5-flash",
                messages=self.request.messages,
            )
        )

        self.assertEqual("你好，我在。", response.content)
        self.assertEqual({"prompt_tokens": 6, "completion_tokens": 5, "total_tokens": 11}, response.usage)
        self.assertEqual(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
            calls[0][0],
        )
        self.assertEqual("gemini-secret", calls[0][1]["x-goog-api-key"])
        self.assertEqual("model", calls[0][2]["contents"][1]["role"])
        self.assertEqual("你好，我在。", calls[0][2]["contents"][1]["parts"][0]["text"])

    def test_native_adapters_fail_closed_on_malformed_response(self) -> None:
        def transport(url, headers, body, timeout_seconds):
            return {"candidates": []}

        adapter = GeminiAdapter(
            GeminiConfig(
                provider_id="gemini",
                base_url="https://example.invalid",
                api_key="secret",
                allowed_models=frozenset({"model"}),
            ),
            transport=transport,
        )

        with self.assertRaisesRegex(AdapterError, "provider response has no assistant message"):
            adapter.chat(ChatRequest("gemini", "model", (ChatMessage("user", "hi"),)))

    def test_anthropic_adapter_runs_through_the_shared_gateway(self) -> None:
        adapter = AnthropicAdapter(
            AnthropicConfig(
                provider_id="anthropic",
                base_url="https://example.invalid",
                api_key="secret",
                allowed_models=frozenset({"claude-sonnet-4-5"}),
            ),
            transport=lambda url, headers, body, timeout_seconds: {
                "id": "msg_gateway",
                "content": [{"type": "text", "text": "网关回复"}],
            },
        )

        response = ProviderGateway(
            ProviderCatalog.default(),
            mode="development",
            adapters={"anthropic": adapter},
        ).chat(
            ChatRequest(
                "anthropic",
                "claude-sonnet-4-5",
                (ChatMessage("user", "你好"),),
            )
        )

        self.assertEqual("网关回复", response.content)


if __name__ == "__main__":
    unittest.main()
