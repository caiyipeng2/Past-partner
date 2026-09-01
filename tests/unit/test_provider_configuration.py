import unittest

from src.providers.catalog import ProviderCatalog
from src.providers.configuration import build_openai_compatible_adapters, build_provider_adapters
from src.providers.qwen_fine_tuning import QwenFineTuningAdapter


class ProviderConfigurationTests(unittest.TestCase):
    def test_builds_required_chinese_provider_adapters_from_environment(self) -> None:
        adapters = build_openai_compatible_adapters(
            ProviderCatalog.default(),
            {
                "PAST_PARTNER_DEEPSEEK_API_KEY": "deepseek-secret",
                "PAST_PARTNER_XIAOMI_MIMO_API_KEY": "mimo-secret",
                "PAST_PARTNER_QWEN_API_KEY": "qwen-secret",
            },
        )

        self.assertEqual(
            {"deepseek", "xiaomi_mimo", "qwen"},
            set(adapters),
        )
        self.assertEqual("https://api.deepseek.com", adapters["deepseek"].config.base_url)
        self.assertEqual("https://api.xiaomimimo.com/v1", adapters["xiaomi_mimo"].config.base_url)
        self.assertEqual(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            adapters["qwen"].config.base_url,
        )
        self.assertTrue(adapters["deepseek"].supports_model("deepseek-v4-flash"))
        self.assertTrue(adapters["xiaomi_mimo"].supports_model("mimo-v2.5-pro"))

    def test_builds_native_and_openai_provider_adapters_from_environment(self) -> None:
        adapters = build_provider_adapters(
            ProviderCatalog.default(),
            {
                "PAST_PARTNER_OPENAI_API_KEY": "openai-secret",
                "PAST_PARTNER_ANTHROPIC_API_KEY": "anthropic-secret",
                "PAST_PARTNER_ANTHROPIC_MODELS": "claude-sonnet-4-5",
                "PAST_PARTNER_GEMINI_API_KEY": "gemini-secret",
                "PAST_PARTNER_GEMINI_MODELS": "gemini-2.5-flash",
            },
        )

        self.assertEqual(
            {"openai", "anthropic", "gemini"},
            set(adapters),
        )
        self.assertEqual("https://api.openai.com/v1", adapters["openai"].config.base_url)
        self.assertEqual("https://api.anthropic.com", adapters["anthropic"].config.base_url)
        self.assertEqual(
            "https://generativelanguage.googleapis.com",
            adapters["gemini"].config.base_url,
        )
        self.assertTrue(adapters["anthropic"].supports_model("claude-sonnet-4-5"))
        self.assertTrue(adapters["gemini"].supports_model("gemini-2.5-flash"))

    def test_custom_openai_provider_requires_endpoint_and_models(self) -> None:
        incomplete = build_openai_compatible_adapters(
            ProviderCatalog.default(),
            {"PAST_PARTNER_CUSTOM_OPENAI_BASE_URL": "https://models.example/v1"},
        )
        self.assertNotIn("custom_openai", incomplete)

        complete = build_openai_compatible_adapters(
            ProviderCatalog.default(),
            {
                "PAST_PARTNER_CUSTOM_OPENAI_BASE_URL": "https://models.example/v1",
                "PAST_PARTNER_CUSTOM_OPENAI_API_KEY": "custom-secret",
                "PAST_PARTNER_CUSTOM_OPENAI_MODELS": "model-a, model-b",
            },
        )
        self.assertTrue(complete["custom_openai"].supports_model("model-b"))

    def test_custom_openai_provider_allows_keyless_local_endpoint(self) -> None:
        adapters = build_openai_compatible_adapters(
            ProviderCatalog.default(),
            {
                "PAST_PARTNER_CUSTOM_OPENAI_BASE_URL": "http://127.0.0.1:11434/v1",
                "PAST_PARTNER_CUSTOM_OPENAI_MODELS": "local-model",
            },
        )

        self.assertIn("custom_openai", adapters)
        self.assertIsNone(adapters["custom_openai"].config.api_key)

    def test_catalog_marks_only_runtime_adapters_as_configured(self) -> None:
        catalog = ProviderCatalog.default().with_configured({"deepseek", "qwen"})

        self.assertTrue(catalog.provider("deepseek").configured)
        self.assertTrue(catalog.provider("qwen").configured)
        self.assertFalse(catalog.provider("xiaomi_mimo").configured)

    def test_qwen_fine_tuning_is_explicitly_opt_in_and_keeps_chat_endpoint(self) -> None:
        adapters = build_provider_adapters(
            ProviderCatalog.default(),
            {
                "PAST_PARTNER_QWEN_API_KEY": "qwen-secret",
                "PAST_PARTNER_QWEN_FINE_TUNING_ENABLED": "true",
                "PAST_PARTNER_QWEN_FINE_TUNING_MODELS": "qwen3.7-plus",
            },
        )

        self.assertIsInstance(adapters["qwen"], QwenFineTuningAdapter)
        adapter = adapters["qwen"]
        assert isinstance(adapter, QwenFineTuningAdapter)
        self.assertEqual("https://dashscope.aliyuncs.com/api/v1", adapter.config.base_url)
        self.assertEqual(
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            adapter.config.chat_base_url,
        )
        self.assertTrue(adapter.supports_fine_tuning("qwen3.7-plus"))

    def test_qwen_fine_tuning_without_explicit_model_allowlist_stays_disabled(self) -> None:
        adapters = build_provider_adapters(
            ProviderCatalog.default(),
            {
                "PAST_PARTNER_QWEN_API_KEY": "qwen-secret",
                "PAST_PARTNER_QWEN_FINE_TUNING_ENABLED": "true",
            },
        )

        self.assertNotIsInstance(adapters["qwen"], QwenFineTuningAdapter)


if __name__ == "__main__":
    unittest.main()
