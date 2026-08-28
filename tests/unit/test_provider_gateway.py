from dataclasses import replace
import base64
import os
from pathlib import Path
import tempfile
import unittest

from src.providers.base import (
    AdapterError,
    ChatMessage,
    ChatRequest,
    MediaAnalysisRequest,
    MediaAnalysisResult,
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


class _MediaAnalysisAdapter:
    provider_id = "test"

    def supports_media(self, model_id: str, media_category: str) -> bool:
        return model_id == "deterministic-vision" and media_category == "image"

    def analyze_media(self, request: MediaAnalysisRequest) -> MediaAnalysisResult:
        return MediaAnalysisResult(
            provider_id=self.provider_id,
            model_id=request.model_id,
            media_type=request.media_type,
            description="测试媒体描述",
            usage={"media_units": 1},
            provider_request_id="media-test-1",
        )


class _FailingMediaAnalysisAdapter(_MediaAnalysisAdapter):
    def analyze_media(self, request: MediaAnalysisRequest) -> MediaAnalysisResult:
        raise AdapterError("provider_unavailable", "provider could not be reached")


class _WrongIdentityMediaAnalysisAdapter(_MediaAnalysisAdapter):
    provider_id = "deepseek"


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

    def _catalog_with_media_analysis(self) -> ProviderCatalog:
        base_provider = self.catalog.provider("deepseek")
        base_model = self.catalog.find_model("deepseek", "deepseek-v4-flash")
        assert base_model is not None
        provider = replace(
            base_provider,
            id="test",
            display_name="Deterministic media test",
            capabilities=("chat", "vision"),
            models=(
                replace(
                    base_model,
                    id="deterministic-vision",
                    capabilities=("chat", "vision"),
                ),
            ),
        )
        return ProviderCatalog((provider,))

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

    def test_media_analysis_is_capability_gated_and_returns_normalized_result(self) -> None:
        gateway = ProviderGateway(
            self._catalog_with_media_analysis(),
            mode="test",
            adapters={"test": _MediaAnalysisAdapter()},
        )
        request = MediaAnalysisRequest(
            provider_id="test",
            model_id="deterministic-vision",
            media_type="image/png",
            media_path=Path("image.png"),
            prompt="描述图片",
        )

        result = gateway.analyze_media(request)

        self.assertEqual("测试媒体描述", result.description)
        self.assertEqual("media-test-1", result.provider_request_id)

    def test_media_analysis_rejects_missing_capability_adapter_and_translates_errors(self) -> None:
        request = MediaAnalysisRequest(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            media_type="image/png",
            media_path=Path("image.png"),
            prompt="描述图片",
        )

        with self.assertRaises(ProviderError) as missing_capability:
            ProviderGateway(self.catalog, mode="development").analyze_media(request)
        self.assertEqual("capability_not_supported", missing_capability.exception.code)

        media_catalog = self._catalog_with_media_analysis()
        with self.assertRaises(ProviderError) as missing_adapter:
            ProviderGateway(media_catalog, mode="test").analyze_media(
                replace(request, provider_id="test", model_id="deterministic-vision")
            )
        self.assertEqual("provider_not_configured", missing_adapter.exception.code)

        with self.assertRaises(ProviderError) as translated:
            ProviderGateway(
                media_catalog,
                mode="test",
                adapters={"test": _FailingMediaAnalysisAdapter()},
            ).analyze_media(
                replace(request, provider_id="test", model_id="deterministic-vision")
            )
        self.assertEqual("provider_unavailable", translated.exception.code)

    def test_media_analysis_rejects_unknown_model_and_mismatched_adapter(self) -> None:
        request = MediaAnalysisRequest(
            provider_id="test",
            model_id="not-real",
            media_type="image/png",
            media_path=Path("image.png"),
            prompt="描述图片",
        )
        gateway = ProviderGateway(
            self._catalog_with_media_analysis(),
            mode="test",
            adapters={"test": _MediaAnalysisAdapter()},
        )
        with self.assertRaises(ProviderError) as unknown_model:
            gateway.analyze_media(request)
        self.assertEqual("unknown_model", unknown_model.exception.code)

        with self.assertRaises(ProviderError) as wrong_identity:
            ProviderGateway(
                self._catalog_with_media_analysis(),
                mode="test",
                adapters={"test": _WrongIdentityMediaAnalysisAdapter()},
            ).analyze_media(replace(request, model_id="deterministic-vision"))
        self.assertEqual("invalid_provider_adapter", wrong_identity.exception.code)

    def test_media_analysis_rejects_unsupported_media_category_before_adapter(self) -> None:
        request = MediaAnalysisRequest(
            provider_id="test",
            model_id="deterministic-vision",
            media_type="application/pdf",
            media_path=Path("document.pdf"),
            prompt="描述文件",
        )
        with self.assertRaises(ProviderError) as captured:
            ProviderGateway(
                self._catalog_with_media_analysis(),
                mode="test",
                adapters={"test": _MediaAnalysisAdapter()},
            ).analyze_media(request)
        self.assertEqual("unsupported_media_category", captured.exception.code)

    def test_openai_compatible_media_analysis_uses_bounded_image_data_url(self) -> None:
        calls = []
        payload = b"fake-image-bytes"

        def fake_transport(url, headers, body, timeout_seconds):
            calls.append((url, headers, body, timeout_seconds))
            return {
                "id": "media-response-1",
                "choices": [{"message": {"content": "图片描述"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
            }

        adapter = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                provider_id="openai",
                base_url="https://example.invalid/v1",
                api_key="test-key",
                allowed_models=frozenset({"gpt-4.1-mini"}),
            ),
            transport=fake_transport,
        )
        gateway = ProviderGateway(self.catalog, mode="development", adapters={"openai": adapter})
        source_fd, source_name = tempfile.mkstemp(suffix=".png")
        os.close(source_fd)
        source_path = Path(source_name)
        source_path.write_bytes(payload)
        self.addCleanup(lambda: source_path.unlink(missing_ok=True))
        result = gateway.analyze_media(
            MediaAnalysisRequest(
                provider_id="openai",
                model_id="gpt-4.1-mini",
                media_type="image/png",
                media_path=source_path,
                prompt="描述图片",
            )
        )

        self.assertEqual("图片描述", result.description)
        self.assertEqual("media-response-1", result.provider_request_id)
        self.assertEqual("https://example.invalid/v1/chat/completions", calls[0][0])
        body = calls[0][2]
        self.assertEqual({"model", "messages", "stream"}, set(body))
        content = body["messages"][0]["content"]
        self.assertEqual(
            "描述图片",
            next(item["text"] for item in content if item["type"] == "text"),
        )
        image = next(item for item in content if item["type"] == "image_url")
        self.assertEqual(
            "data:image/png;base64," + base64.b64encode(payload).decode("ascii"),
            image["image_url"]["url"],
        )
        self.assertEqual("Bearer test-key", calls[0][1]["Authorization"])

    def test_openai_compatible_media_analysis_maps_response_and_transport_errors(self) -> None:
        request_fd, request_name = tempfile.mkstemp(suffix=".png")
        os.close(request_fd)
        request_path = Path(request_name)
        request_path.write_bytes(b"image")
        self.addCleanup(lambda: request_path.unlink(missing_ok=True))
        request = MediaAnalysisRequest(
            provider_id="openai",
            model_id="gpt-4.1-mini",
            media_type="image/png",
            media_path=request_path,
            prompt="描述图片",
        )

        malformed = OpenAICompatibleAdapter(
            OpenAICompatibleConfig("openai", "https://example.invalid/v1", "key", frozenset({"gpt-4.1-mini"})),
            transport=lambda *_args: {"choices": []},
        )
        with self.assertRaises(AdapterError) as malformed_error:
            malformed.analyze_media(request)
        self.assertEqual("invalid_provider_response", malformed_error.exception.code)

        for code in ("provider_timeout", "provider_rate_limited"):
            adapter = OpenAICompatibleAdapter(
                OpenAICompatibleConfig("openai", "https://example.invalid/v1", "key", frozenset({"gpt-4.1-mini"})),
                transport=lambda *_args, error_code=code: (_ for _ in ()).throw(
                    AdapterError(error_code, "provider failure")
                ),
            )
            with self.assertRaises(AdapterError) as translated:
                adapter.analyze_media(request)
            self.assertEqual(code, translated.exception.code)

    def test_openai_compatible_media_analysis_rejects_audio_and_oversized_files(self) -> None:
        adapter = OpenAICompatibleAdapter(
            OpenAICompatibleConfig(
                "openai",
                "https://example.invalid/v1",
                "key",
                frozenset({"gpt-4.1-mini"}),
                max_media_bytes=4,
            ),
            transport=lambda *_args: (_ for _ in ()).throw(AssertionError("must not transport")),
        )
        audio_fd, audio_name = tempfile.mkstemp(suffix=".wav")
        os.close(audio_fd)
        audio_path = Path(audio_name)
        audio_path.write_bytes(b"audio")
        self.addCleanup(lambda: audio_path.unlink(missing_ok=True))
        with self.assertRaises(AdapterError) as unsupported:
            adapter.analyze_media(
                MediaAnalysisRequest(
                    "openai",
                    "gpt-4.1-mini",
                    "audio/wav",
                    audio_path,
                    "描述音频",
                )
            )
        self.assertEqual("capability_not_supported", unsupported.exception.code)

        oversized_fd, oversized_name = tempfile.mkstemp(suffix=".png")
        os.close(oversized_fd)
        oversized_path = Path(oversized_name)
        oversized_path.write_bytes(b"12345")
        self.addCleanup(lambda: oversized_path.unlink(missing_ok=True))
        with self.assertRaises(AdapterError) as size_error:
            adapter.analyze_media(
                MediaAnalysisRequest(
                    "openai",
                    "gpt-4.1-mini",
                    "image/png",
                    oversized_path,
                    "描述图片",
                )
            )
        self.assertEqual("media_too_large", size_error.exception.code)

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
