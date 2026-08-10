import unittest

from src.providers.catalog import ProviderCatalog
from src.providers.configuration import build_openai_compatible_adapters, build_provider_adapters


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

    def test_custom_openai_provider_requires_endpoint_key_and_models(self) -> None:
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

    def test_catalog_marks_only_runtime_adapters_as_configured(self) -> None:
        catalog = ProviderCatalog.default().with_configured({"deepseek", "qwen"})

        self.assertTrue(catalog.provider("deepseek").configured)
        self.assertTrue(catalog.provider("qwen").configured)
        self.assertFalse(catalog.provider("xiaomi_mimo").configured)


if __name__ == "__main__":
    unittest.main()
