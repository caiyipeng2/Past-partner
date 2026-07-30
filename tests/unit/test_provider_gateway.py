import unittest

from src.providers.base import ChatMessage, ChatRequest
from src.providers.catalog import ProviderCatalog
from src.providers.gateway import ProviderError, ProviderGateway
from src.providers.openai_compatible import OpenAICompatibleAdapter, OpenAICompatibleConfig
from src.providers.testing import DeterministicTestAdapter


class ProviderGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ProviderCatalog.default()
        self.request = ChatRequest(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            messages=(ChatMessage(role="user", content="你好"),),
        )

    def test_unconfigured_real_provider_fails_truthfully(self) -> None:
        gateway = ProviderGateway(self.catalog, mode="development")

        with self.assertRaises(ProviderError) as captured:
            gateway.chat(self.request)
        self.assertEqual("provider_not_configured", captured.exception.code)

    def test_unknown_provider_and_model_return_stable_codes(self) -> None:
        gateway = ProviderGateway(self.catalog, mode="development")
        unknown_provider = ChatRequest("not-real", "model", self.request.messages)
        with self.assertRaises(ProviderError) as captured:
            gateway.chat(unknown_provider)
        self.assertEqual("unknown_provider", captured.exception.code)

        unknown_model = ChatRequest("deepseek", "not-real", self.request.messages)
        with self.assertRaises(ProviderError) as captured:
            gateway.chat(unknown_model)
        self.assertEqual("unknown_model", captured.exception.code)

    def test_deterministic_adapter_is_test_mode_only(self) -> None:
        with self.assertRaises(ProviderError) as captured:
            ProviderGateway(
                self.catalog,
                mode="development",
                adapters={"test": DeterministicTestAdapter()},
            )
        self.assertEqual("test_provider_disabled", captured.exception.code)

        gateway = ProviderGateway(
            self.catalog,
            mode="test",
            adapters={"test": DeterministicTestAdapter()},
        )
        response = gateway.chat(ChatRequest("test", "deterministic", self.request.messages))
        self.assertEqual("测试回复：你好", response.content)

    def test_openai_compatible_adapter_uses_injected_transport(self) -> None:
        calls = []

        def fake_transport(url, headers, body, timeout_seconds):
            calls.append((url, headers, body, timeout_seconds))
            return {
                "id": "chatcmpl-test",
                "choices": [{"message": {"content": "你好，我在。"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
            }

        adapter = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                provider_id="deepseek",
                base_url="https://example.invalid/v1",
                api_key="test-key",
                allowed_models=frozenset({"deepseek-v4-flash"}),
            ),
            transport=fake_transport,
        )
        gateway = ProviderGateway(
            self.catalog,
            mode="development",
            adapters={"deepseek": adapter},
        )

        response = gateway.chat(self.request)

        self.assertEqual("你好，我在。", response.content)
        self.assertEqual("https://example.invalid/v1/chat/completions", calls[0][0])
        self.assertEqual("Bearer test-key", calls[0][1]["Authorization"])
        self.assertEqual("deepseek-v4-flash", calls[0][2]["model"])


if __name__ == "__main__":
    unittest.main()
