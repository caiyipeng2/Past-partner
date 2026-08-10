import unittest

from src.providers.catalog import CatalogValidationError, ProviderCatalog


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

    def test_model_metadata_and_cost_estimate_use_configured_prices(self) -> None:
        catalog = ProviderCatalog.default().with_pricing(
            {
                "deepseek/deepseek-v4-flash": {
                    "context_length": 128000,
                    "input_price_per_million_tokens": 0.14,
                    "output_price_per_million_tokens": 0.28,
                    "currency": "USD",
                    "source": "admin",
                    "last_refreshed_at": "2026-08-10T00:00:00+00:00",
                }
            }
        )

        model = catalog.find_model("deepseek", "deepseek-v4-flash")
        self.assertIsNotNone(model)
        assert model is not None
        self.assertGreater(model.context_length, 0)
        self.assertIn("text", model.capabilities)
        self.assertEqual("admin", model.pricing.source)
        self.assertEqual("2026-08-10T00:00:00+00:00", model.pricing.last_refreshed_at)

        estimate = catalog.estimate_cost(
            "deepseek",
            "deepseek-v4-flash",
            input_tokens=1_000_000,
            output_tokens=500_000,
        )
        self.assertEqual("USD", estimate.currency)
        self.assertAlmostEqual(0.28, estimate.total_cost)
        self.assertAlmostEqual(0.14, estimate.input_cost)
        self.assertAlmostEqual(0.14, estimate.output_cost)

    def test_unpriced_model_fails_closed_instead_of_fabricating_cost(self) -> None:
        catalog = ProviderCatalog.default()

        with self.assertRaises(CatalogValidationError) as captured:
            catalog.estimate_cost(
                "deepseek",
                "deepseek-v4-flash",
                input_tokens=10,
                output_tokens=20,
            )

        self.assertEqual("pricing_unavailable", captured.exception.code)

    def test_runtime_model_can_receive_the_same_pricing_metadata(self) -> None:
        catalog = ProviderCatalog.default().with_configured(
            {"ollama"},
            {"ollama": frozenset({"llama3.2"})},
        ).with_pricing(
            {
                "ollama/llama3.2": {
                    "input_price_per_million_tokens": 0,
                    "output_price_per_million_tokens": 0,
                    "currency": "USD",
                    "source": "local",
                }
            }
        )

        estimate = catalog.estimate_cost(
            "ollama",
            "llama3.2",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        self.assertEqual("local", catalog.find_model("ollama", "llama3.2").pricing.source)
        self.assertEqual(0, estimate.total_cost)


if __name__ == "__main__":
    unittest.main()
