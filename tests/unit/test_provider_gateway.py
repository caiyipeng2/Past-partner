from dataclasses import replace
from pathlib import Path
import unittest

from src.providers.base import (
    AdapterError,
    ChatMessage,
    ChatRequest,
    FineTuningRequest,
    FineTuningStatus,
    FineTuningSubmission,
)
from src.providers.catalog import ProviderCatalog
from src.providers.gateway import ProviderError, ProviderGateway
from src.providers.openai_compatible import OpenAICompatibleAdapter, OpenAICompatibleConfig
from src.providers.testing import DeterministicTestAdapter, deterministic_test_provider_definition


class _ChatOnlyAdapter:
    provider_id = "deepseek"

    def supports_model(self, model_id: str) -> bool:
        return model_id == "deepseek-v4-flash"

    def chat(self, request: ChatRequest):
        raise AssertionError("fine-tuning must not fall back to chat")


class _FailingFineTuningAdapter(_ChatOnlyAdapter):
    def supports_fine_tuning(self, model_id: str) -> bool:
        return model_id == "deepseek-v4-flash"

    def submit_fine_tuning(self, request: FineTuningRequest) -> FineTuningSubmission:
        raise AdapterError("provider_unavailable", "provider could not be reached")

    def recover_fine_tuning_submission(self, client_job_id: str) -> FineTuningSubmission | None:
        raise AdapterError("provider_unavailable", "provider could not be reached")

    def get_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        raise AdapterError("provider_unavailable", "provider could not be reached")

    def cancel_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        raise AdapterError("provider_unavailable", "provider could not be reached")


class _CapabilityProbeFailureAdapter(_ChatOnlyAdapter):
    def supports_fine_tuning(self, model_id: str) -> bool:
        raise AdapterError("provider_unavailable", "provider could not be reached")

    def submit_fine_tuning(self, request: FineTuningRequest) -> FineTuningSubmission:
        raise AssertionError("the gateway must not submit after a failed capability probe")

    def recover_fine_tuning_submission(self, client_job_id: str) -> FineTuningSubmission | None:
        raise AssertionError("the gateway must not recover after a failed capability probe")

    def get_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        raise AssertionError("the gateway must not fetch after a failed capability probe")

    def cancel_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        raise AssertionError("the gateway must not cancel after a failed capability probe")


class _MalformedFineTuningAdapter(_ChatOnlyAdapter):
    supports_fine_tuning = False

    def submit_fine_tuning(self, request: FineTuningRequest) -> FineTuningSubmission:
        raise AssertionError("the gateway must reject malformed adapters before submission")

    def get_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        raise AssertionError("the gateway must reject malformed adapters before lookup")

    def cancel_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        raise AssertionError("the gateway must reject malformed adapters before cancellation")


class _WrongIdentityFineTuningAdapter(_ChatOnlyAdapter):
    provider_id = "qwen"

    def supports_fine_tuning(self, model_id: str) -> bool:
        return True

    def submit_fine_tuning(self, request: FineTuningRequest) -> FineTuningSubmission:
        raise AssertionError("the gateway must reject a provider identity mismatch before submission")

    def recover_fine_tuning_submission(self, client_job_id: str) -> FineTuningSubmission | None:
        raise AssertionError("the gateway must reject a provider identity mismatch before recovery")

    def get_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        raise AssertionError("the gateway must reject a provider identity mismatch before lookup")

    def cancel_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
        raise AssertionError("the gateway must reject a provider identity mismatch before cancellation")


class _TruthyCapabilityFineTuningAdapter(_FailingFineTuningAdapter):
    def supports_fine_tuning(self, model_id: str):
        return "yes"


class ProviderGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ProviderCatalog.default()
        self.request = ChatRequest(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            messages=(ChatMessage(role="user", content="你好"),),
        )
        self.fine_tuning_request = FineTuningRequest(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            job_id="job-123",
            dataset_path=Path("dataset.jsonl"),
            dataset_sha256="a" * 64,
            sample_count=2,
        )

    def _catalog_with_fine_tuning(self) -> ProviderCatalog:
        provider = self.catalog.provider("deepseek")
        model = self.catalog.find_model("deepseek", "deepseek-v4-flash")
        assert model is not None
        enabled_model = replace(
            model,
            capabilities=(*model.capabilities, "fine_tuning"),
        )
        enabled_provider = replace(
            provider,
            capabilities=(*provider.capabilities, "fine_tuning"),
            models=(enabled_model,),
        )
        return ProviderCatalog((enabled_provider,))

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

        with self.assertRaises(ProviderError) as captured:
            ProviderGateway(
                self.catalog,
                mode="development",
                adapters={"deepseek": DeterministicTestAdapter()},
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

    def test_fine_tuning_rejects_a_model_without_declared_capability(self) -> None:
        gateway = ProviderGateway(self.catalog, mode="development")

        with self.assertRaises(ProviderError) as captured:
            gateway.submit_fine_tuning(self.fine_tuning_request)

        self.assertEqual("capability_not_supported", captured.exception.code)

    def test_fine_tuning_requires_provider_and_model_capabilities(self) -> None:
        provider = self.catalog.provider("deepseek")
        model = self.catalog.find_model("deepseek", "deepseek-v4-flash")
        assert model is not None
        provider_only_catalog = ProviderCatalog(
            (
                replace(
                    provider,
                    capabilities=(*provider.capabilities, "fine_tuning"),
                    models=(model,),
                ),
            )
        )
        gateway = ProviderGateway(provider_only_catalog, mode="development")

        with self.assertRaises(ProviderError) as captured:
            gateway.submit_fine_tuning(self.fine_tuning_request)

        self.assertEqual("capability_not_supported", captured.exception.code)

    def test_fine_tuning_requires_a_compatible_adapter(self) -> None:
        gateway = ProviderGateway(
            self._catalog_with_fine_tuning(),
            mode="development",
            adapters={"deepseek": _ChatOnlyAdapter()},
        )

        with self.assertRaises(ProviderError) as captured:
            gateway.submit_fine_tuning(self.fine_tuning_request)

        self.assertEqual("provider_not_configured", captured.exception.code)

    def test_fine_tuning_translates_adapter_error(self) -> None:
        gateway = ProviderGateway(
            self._catalog_with_fine_tuning(),
            mode="development",
            adapters={"deepseek": _FailingFineTuningAdapter()},
        )

        with self.assertRaises(ProviderError) as captured:
            gateway.submit_fine_tuning(self.fine_tuning_request)

        self.assertEqual("provider_unavailable", captured.exception.code)

    def test_fine_tuning_translates_capability_probe_errors_for_all_operations(self) -> None:
        gateway = ProviderGateway(
            self._catalog_with_fine_tuning(),
            mode="development",
            adapters={"deepseek": _CapabilityProbeFailureAdapter()},
        )

        for operation in (
            lambda: gateway.submit_fine_tuning(self.fine_tuning_request),
            lambda: gateway.recover_fine_tuning_submission(
                "deepseek", "deepseek-v4-flash", "job-123"
            ),
            lambda: gateway.get_fine_tuning_job("deepseek", "deepseek-v4-flash", "provider-job"),
            lambda: gateway.cancel_fine_tuning_job("deepseek", "deepseek-v4-flash", "provider-job"),
        ):
            with self.assertRaises(ProviderError) as captured:
                operation()
            self.assertEqual("provider_unavailable", captured.exception.code)

    def test_fine_tuning_rejects_malformed_adapter_for_all_operations(self) -> None:
        gateway = ProviderGateway(
            self._catalog_with_fine_tuning(),
            mode="development",
            adapters={"deepseek": _MalformedFineTuningAdapter()},
        )

        for operation in (
            lambda: gateway.submit_fine_tuning(self.fine_tuning_request),
            lambda: gateway.recover_fine_tuning_submission(
                "deepseek", "deepseek-v4-flash", "job-123"
            ),
            lambda: gateway.get_fine_tuning_job("deepseek", "deepseek-v4-flash", "provider-job"),
            lambda: gateway.cancel_fine_tuning_job("deepseek", "deepseek-v4-flash", "provider-job"),
        ):
            with self.assertRaises(ProviderError) as captured:
                operation()
            self.assertEqual("invalid_provider_adapter", captured.exception.code)

    def test_fine_tuning_rejects_mismatched_adapter_identity_for_all_operations(self) -> None:
        gateway = ProviderGateway(
            self._catalog_with_fine_tuning(),
            mode="development",
            adapters={"deepseek": _WrongIdentityFineTuningAdapter()},
        )

        for operation in (
            lambda: gateway.submit_fine_tuning(self.fine_tuning_request),
            lambda: gateway.recover_fine_tuning_submission(
                "deepseek", "deepseek-v4-flash", "job-123"
            ),
            lambda: gateway.get_fine_tuning_job("deepseek", "deepseek-v4-flash", "provider-job"),
            lambda: gateway.cancel_fine_tuning_job("deepseek", "deepseek-v4-flash", "provider-job"),
        ):
            with self.assertRaises(ProviderError) as captured:
                operation()
            self.assertEqual("invalid_provider_adapter", captured.exception.code)

    def test_fine_tuning_rechecks_test_provider_policy_after_adapter_mutation(self) -> None:
        gateway = ProviderGateway(self._catalog_with_fine_tuning(), mode="development")
        gateway.adapters["deepseek"] = DeterministicTestAdapter()

        for operation in (
            lambda: gateway.submit_fine_tuning(self.fine_tuning_request),
            lambda: gateway.get_fine_tuning_job("deepseek", "deepseek-v4-flash", "provider-job"),
            lambda: gateway.cancel_fine_tuning_job("deepseek", "deepseek-v4-flash", "provider-job"),
        ):
            with self.assertRaises(ProviderError) as captured:
                operation()
            self.assertEqual("test_provider_disabled", captured.exception.code)

    def test_fine_tuning_rejects_truthy_non_boolean_capability_results(self) -> None:
        gateway = ProviderGateway(
            self._catalog_with_fine_tuning(),
            mode="development",
            adapters={"deepseek": _TruthyCapabilityFineTuningAdapter()},
        )

        for operation in (
            lambda: gateway.submit_fine_tuning(self.fine_tuning_request),
            lambda: gateway.recover_fine_tuning_submission(
                "deepseek", "deepseek-v4-flash", "job-123"
            ),
            lambda: gateway.get_fine_tuning_job("deepseek", "deepseek-v4-flash", "provider-job"),
            lambda: gateway.cancel_fine_tuning_job("deepseek", "deepseek-v4-flash", "provider-job"),
        ):
            with self.assertRaises(ProviderError) as captured:
                operation()
            self.assertEqual("invalid_provider_adapter", captured.exception.code)

    def test_deterministic_fine_tuning_lifecycle_is_test_mode_only(self) -> None:
        test_catalog = ProviderCatalog((*self.catalog.providers(), deterministic_test_provider_definition()))
        gateway = ProviderGateway(
            test_catalog,
            mode="test",
            adapters={"test": DeterministicTestAdapter()},
        )
        request = replace(
            self.fine_tuning_request,
            provider_id="test",
            model_id="deterministic",
        )

        submission = gateway.submit_fine_tuning(request)
        recovered = gateway.recover_fine_tuning_submission("test", "deterministic", "job-123")
        completed = gateway.get_fine_tuning_job("test", "deterministic", submission.provider_job_id)

        self.assertEqual("test-ft-job-123", submission.provider_job_id)
        self.assertIsNotNone(recovered)
        self.assertEqual("test-ft-job-123", recovered.provider_job_id)
        self.assertEqual("completed", completed.state)
        self.assertEqual(100, completed.progress_percent)
        self.assertTrue(completed.artifact_id)
        self.assertEqual({"status": "verified"}, completed.evaluation)
        self.assertFalse(completed.retryable)

        cancel_submission = gateway.submit_fine_tuning(replace(request, job_id="job-cancel"))
        cancelled = gateway.cancel_fine_tuning_job(
            "test",
            "deterministic",
            cancel_submission.provider_job_id,
        )
        self.assertEqual("cancelled", cancelled.state)
        self.assertFalse(cancelled.retryable)

        disabled_gateway = ProviderGateway(test_catalog, mode="development")
        with self.assertRaises(ProviderError) as captured:
            disabled_gateway.submit_fine_tuning(request)
        self.assertEqual("test_provider_disabled", captured.exception.code)

    def test_test_mode_fine_tuning_also_requires_catalog_capability(self) -> None:
        gateway = ProviderGateway(
            self.catalog,
            mode="test",
            adapters={"test": DeterministicTestAdapter()},
        )
        request = replace(
            self.fine_tuning_request,
            provider_id="test",
            model_id="deterministic",
        )

        with self.assertRaises(ProviderError) as captured:
            gateway.submit_fine_tuning(request)

        self.assertEqual("unknown_provider", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
