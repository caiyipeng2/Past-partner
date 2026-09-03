import unittest

from src.domain.consents import ConsentValidationError, MediaConsent
from src.providers.catalog import ProviderCatalog
from src.services.multimodal_consent import MultimodalConsentGate


class StubConsentService:
    def __init__(self, consent: MediaConsent):
        self.consent = consent
        self.calls = []

    def authorize(self, owner_id, consent_id, **scope):
        self.calls.append((owner_id, consent_id, scope))
        if consent_id != self.consent.id:
            raise AssertionError("unexpected consent id")
        if self.consent.status != "active":
            raise ConsentValidationError("consent_revoked", "consent is revoked")
        expected = {
            "provider_id": self.consent.provider_id,
            "model_id": self.consent.model_id,
            "data_category": self.consent.data_category,
            "authorization_scope": self.consent.authorization_scope,
        }
        if scope != expected:
            raise ConsentValidationError("consent_scope_mismatch", "consent scope mismatch")
        return self.consent


def _consent(provider_id: str, model_id: str, data_category: str) -> MediaConsent:
    return MediaConsent.create(
        persona_id="persona-1",
        provider_id=provider_id,
        model_id=model_id,
        data_category=data_category,
        estimated_cost=0,
        purpose="approved media processing",
        authorization_scope=f"persona-{data_category}-analysis",
        created_at="2026-08-11T00:00:00+00:00",
        consent_id="consent-1",
    )


class MultimodalConsentTests(unittest.TestCase):
    def test_authorizes_a_vision_request_only_after_capability_and_scope_checks(self) -> None:
        consent = _consent("openai", "gpt-4.1-mini", "image")
        gate = MultimodalConsentGate(StubConsentService(consent), ProviderCatalog.default())

        decision = gate.authorize(
            owner_id="owner-1",
            consent_id=consent.id,
            provider_id="openai",
            model_id="gpt-4.1-mini",
            data_category="image",
            authorization_scope=consent.authorization_scope,
        )

        self.assertTrue(decision.authorized)
        self.assertEqual("vision", decision.required_capability)
        self.assertIn("vision", decision.model_capabilities)
        self.assertEqual(consent.id, decision.consent_id)
        self.assertEqual("vision", decision.to_dict()["required_capability"])

    def test_rejects_a_model_that_does_not_advertise_the_required_capability(self) -> None:
        consent = _consent("deepseek", "deepseek-v4-flash", "image")
        gate = MultimodalConsentGate(StubConsentService(consent), ProviderCatalog.default())

        with self.assertRaises(ConsentValidationError) as raised:
            gate.authorize(
                owner_id="owner-1",
                consent_id=consent.id,
                provider_id="deepseek",
                model_id="deepseek-v4-flash",
                data_category="image",
                authorization_scope=consent.authorization_scope,
            )

        self.assertEqual("model_capability_missing", raised.exception.code)

    def test_rejects_unknown_models_and_ambiguous_non_media_categories(self) -> None:
        consent = _consent("openai", "gpt-4.1-mini", "text")
        gate = MultimodalConsentGate(StubConsentService(consent), ProviderCatalog.default())

        with self.assertRaises(ConsentValidationError) as category_error:
            gate.authorize(
                owner_id="owner-1",
                consent_id=consent.id,
                provider_id="openai",
                model_id="gpt-4.1-mini",
                data_category="text",
                authorization_scope=consent.authorization_scope,
            )
        self.assertEqual("unsupported_media_category", category_error.exception.code)

        with self.assertRaises(ConsentValidationError) as model_error:
            gate.authorize(
                owner_id="owner-1",
                consent_id=consent.id,
                provider_id="openai",
                model_id="not-in-catalog",
                data_category="image",
                authorization_scope="persona-image-analysis",
            )
        self.assertEqual("unknown_model", model_error.exception.code)

    def test_ocr_requires_dedicated_purpose_and_reports_ocr_capability(self) -> None:
        consent = MediaConsent.create(
            persona_id="persona-1",
            provider_id="custom_openai",
            model_id="ocr-model",
            data_category="image",
            estimated_cost=0,
            purpose="image_ocr",
            authorization_scope="persona-image-ocr",
            created_at="2026-08-11T00:00:00+00:00",
            consent_id="consent-ocr",
        )
        catalog = ProviderCatalog.default().with_configured(
            {"custom_openai"},
            {"custom_openai": frozenset({"ocr-model"})},
            media_capabilities={"custom_openai": {"ocr-model": frozenset({"ocr"})}},
        )
        gate = MultimodalConsentGate(StubConsentService(consent), catalog)

        decision = gate.authorize(
            owner_id="owner-1",
            consent_id=consent.id,
            provider_id="custom_openai",
            model_id="ocr-model",
            data_category="image",
            analysis_kind="ocr",
            authorization_scope=consent.authorization_scope,
        )

        self.assertTrue(decision.authorized)
        self.assertEqual("ocr", decision.required_capability)
        self.assertEqual("ocr", decision.to_dict()["required_capability"])

        ordinary_image = _consent("custom_openai", "ocr-model", "image")
        ordinary_gate = MultimodalConsentGate(StubConsentService(ordinary_image), catalog)
        with self.assertRaises(ConsentValidationError) as mismatch:
            ordinary_gate.authorize(
                owner_id="owner-1",
                consent_id=ordinary_image.id,
                provider_id="custom_openai",
                model_id="ocr-model",
                data_category="image",
                analysis_kind="ocr",
                authorization_scope=ordinary_image.authorization_scope,
            )
        self.assertEqual("consent_scope_mismatch", mismatch.exception.code)


if __name__ == "__main__":
    unittest.main()
