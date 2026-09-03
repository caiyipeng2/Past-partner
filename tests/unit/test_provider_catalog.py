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

    def test_training_cost_requires_model_training_price(self) -> None:
        catalog = ProviderCatalog.default()

        with self.assertRaises(CatalogValidationError) as captured:
            catalog.estimate_training_cost(
                "deepseek",
                "deepseek-v4-flash",
                training_tokens=12,
            )

        self.assertEqual("pricing_unavailable", captured.exception.code)

    def test_training_cost_uses_configured_price_and_serializes_it(self) -> None:
        catalog = ProviderCatalog.default().with_pricing(
            {
                "deepseek/deepseek-v4-flash": {
                    "training_price_per_million_tokens": 1.2,
                    "currency": "USD",
                    "source": "admin",
                    "last_refreshed_at": "2026-08-11T00:00:00+00:00",
                }
            }
        )

        estimate = catalog.estimate_training_cost(
            "deepseek",
            "deepseek-v4-flash",
            training_tokens=500_000,
        )
        model = catalog.find_model("deepseek", "deepseek-v4-flash")

        self.assertEqual(500_000, estimate.training_tokens)
        self.assertAlmostEqual(0.6, estimate.estimated_cost)
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual(1.2, model.to_dict()["pricing"]["training_price_per_million_tokens"])

    def test_default_catalog_does_not_advertise_fine_tuning(self) -> None:
        model = ProviderCatalog.default().find_model("deepseek", "deepseek-v4-flash")

        self.assertIsNotNone(model)
        assert model is not None
        self.assertNotIn("fine_tuning", model.capabilities)

    def test_runtime_fine_tuning_capabilities_are_added_only_for_explicit_models(self) -> None:
        catalog = ProviderCatalog.default().with_configured(
            {"qwen"},
            fine_tuning_models={"qwen": frozenset({"qwen3.7-plus"})},
        )

        provider = catalog.provider("qwen")
        model = catalog.find_model("qwen", "qwen3.7-plus")
        other = catalog.find_model("qwen", "qwen3.7-max")
        self.assertIn("fine_tuning", provider.capabilities)
        self.assertIsNotNone(model)
        self.assertIsNotNone(other)
        assert model is not None and other is not None
        self.assertIn("fine_tuning", model.capabilities)
        self.assertNotIn("fine_tuning", other.capabilities)

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

    def test_explicit_media_capabilities_are_added_only_to_selected_models(self) -> None:
        catalog = ProviderCatalog.default().with_configured(
            {"custom_openai"},
            {"custom_openai": frozenset({"audio-model", "text-model"})},
            media_capabilities={"custom_openai": {"audio-model": frozenset({"audio"})}},
        )

        provider = catalog.provider("custom_openai")
        audio_model = catalog.find_model("custom_openai", "audio-model")
        text_model = catalog.find_model("custom_openai", "text-model")
        self.assertIn("audio", provider.capabilities)
        self.assertIsNotNone(audio_model)
        self.assertIsNotNone(text_model)
        assert audio_model is not None and text_model is not None
        self.assertIn("audio", audio_model.capabilities)
        self.assertNotIn("audio", text_model.capabilities)

    def test_explicit_ocr_capability_is_added_only_to_selected_models(self) -> None:
        catalog = ProviderCatalog.default().with_configured(
            {"custom_openai"},
            {"custom_openai": frozenset({"ocr-model", "vision-model"})},
            media_capabilities={"custom_openai": {"ocr-model": frozenset({"ocr"})}},
        )

        provider = catalog.provider("custom_openai")
        ocr_model = catalog.find_model("custom_openai", "ocr-model")
        vision_model = catalog.find_model("custom_openai", "vision-model")
        self.assertIn("ocr", provider.capabilities)
        self.assertIsNotNone(ocr_model)
        self.assertIsNotNone(vision_model)
        assert ocr_model is not None and vision_model is not None
        self.assertIn("ocr", ocr_model.capabilities)
        self.assertNotIn("ocr", vision_model.capabilities)

    def test_explicit_runtime_model_can_extend_a_static_provider_catalog(self) -> None:
        catalog = ProviderCatalog.default().with_configured(
            {"openai"},
            {"openai": frozenset({"whisper-1"})},
            media_capabilities={"openai": {"whisper-1": frozenset({"audio"})}},
        )

        provider = catalog.provider("openai")
        model = catalog.find_model("openai", "whisper-1")
        self.assertIn("audio", provider.capabilities)
        self.assertIsNotNone(model)
        assert model is not None
        self.assertIn("audio", model.capabilities)

    def test_explicit_runtime_model_can_receive_video_capability(self) -> None:
        catalog = ProviderCatalog.default().with_configured(
            {"openai"},
            {"openai": frozenset({"video-model"})},
            media_capabilities={"openai": {"video-model": frozenset({"video"})}},
        )

        provider = catalog.provider("openai")
        model = catalog.find_model("openai", "video-model")
        self.assertIn("video", provider.capabilities)
        self.assertIsNotNone(model)
        assert model is not None
        self.assertIn("video", model.capabilities)

    def test_runtime_qwen_model_can_receive_audio_and_fine_tuning_capabilities(self) -> None:
        catalog = ProviderCatalog.default().with_configured(
            {"qwen"},
            {"qwen": frozenset({"qwen-audio"})},
            {"qwen": frozenset({"qwen-audio"})},
            media_capabilities={"qwen": {"qwen-audio": frozenset({"audio"})}},
        )

        provider = catalog.provider("qwen")
        model = catalog.find_model("qwen", "qwen-audio")
        self.assertIn("audio", provider.capabilities)
        self.assertIn("fine_tuning", provider.capabilities)
        self.assertIsNotNone(model)
        assert model is not None
        self.assertIn("audio", model.capabilities)
        self.assertIn("fine_tuning", model.capabilities)


if __name__ == "__main__":
    unittest.main()
