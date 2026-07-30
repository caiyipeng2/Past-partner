import unittest

from src.providers.catalog import ProviderCatalog


class ProviderCatalogTests(unittest.TestCase):
    def test_contains_required_provider_families(self) -> None:
        catalog = ProviderCatalog.default()

        self.assertEqual(
            {
                "openai",
                "anthropic",
                "gemini",
                "deepseek",
                "xiaomi_mimo",
                "qwen",
                "ollama",
                "custom_openai",
                "custom_http",
            },
            {provider.id for provider in catalog.providers()},
        )

    def test_catalog_describes_capabilities_and_pricing_source(self) -> None:
        deepseek = ProviderCatalog.default().provider("deepseek")

        self.assertIn("chat", deepseek.capabilities)
        self.assertEqual("provider", deepseek.pricing_source)
        self.assertEqual("byok", deepseek.credential_mode)
        self.assertFalse(deepseek.configured)

    def test_unknown_provider_and_model_are_not_silently_accepted(self) -> None:
        catalog = ProviderCatalog.default()

        self.assertIsNone(catalog.find_provider("not-real"))
        self.assertIsNone(catalog.find_model("deepseek", "not-real"))


if __name__ == "__main__":
    unittest.main()
