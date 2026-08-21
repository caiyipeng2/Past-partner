from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from src.providers.base import FineTuningRequest
from src.providers.qwen_fine_tuning import QwenFineTuningAdapter, QwenFineTuningConfig
from src.providers.base import AdapterError


class QwenFineTuningAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.json_calls: list[tuple[str, str, dict[str, str], dict[str, object]]] = []
        self.multipart_calls: list[tuple[str, dict[str, str], dict[str, str], Path]] = []

        def json_request(method, url, headers, body, timeout_seconds):
            self.json_calls.append((method, url, headers, body))
            if method == "POST" and url.endswith("/fine-tunes"):
                return {"output": {"job_id": "ft-qwen-123", "job_name": "local-job"}}
            if method == "GET" and url.endswith("/fine-tunes?page_no=1&page_size=1000"):
                return {
                    "output": {
                        "total": 1,
                        "jobs": [{"job_id": "ft-qwen-123", "job_name": "local-job"}],
                    }
                }
            if method == "GET" and url.endswith("/fine-tunes/ft-qwen-123"):
                return {
                    "output": {
                        "job_id": "ft-qwen-123",
                        "status": "SUCCEEDED",
                        "finetuned_output": "qwen-ft-model",
                        "usage": 8192,
                        "output_cnt": 1,
                    }
                }
            if method == "POST" and url.endswith("/fine-tunes/ft-qwen-123/cancel"):
                return {"output": {"status": "success"}}
            raise AssertionError(f"unexpected request: {method} {url}")

        def multipart(url, headers, fields, file_field, file_path, timeout_seconds):
            self.multipart_calls.append((url, headers, fields, file_path))
            self.assertEqual("files", file_field)
            return {"data": {"uploaded_files": [{"file_id": "file-qwen-123", "name": "dataset.jsonl"}]}}

        self.adapter = QwenFineTuningAdapter(
            QwenFineTuningConfig(
                provider_id="qwen",
                base_url="https://dashscope.example/api/v1",
                api_key="secret-key",
                allowed_models=frozenset({"qwen3.7-plus"}),
                fine_tuning_models=frozenset({"qwen3.7-plus"}),
            ),
            json_request=json_request,
            multipart_request=multipart,
        )

    def _request(self, path: Path) -> FineTuningRequest:
        return FineTuningRequest(
            provider_id="qwen",
            model_id="qwen3.7-plus",
            job_id="local-job",
            dataset_path=path,
            dataset_sha256="a" * 64,
            sample_count=3,
        )

    def test_submit_uploads_jsonl_then_creates_job_with_local_recovery_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text('{"messages": []}\n', encoding="utf-8")

            submission = self.adapter.submit_fine_tuning(self._request(path))

        self.assertEqual("ft-qwen-123", submission.provider_job_id)
        self.assertEqual(1, len(self.multipart_calls))
        upload_url, headers, fields, uploaded_path = self.multipart_calls[0]
        self.assertEqual("https://dashscope.example/api/v1/files", upload_url)
        self.assertEqual("Bearer secret-key", headers["Authorization"])
        self.assertEqual({"purpose": "fine-tune"}, fields)
        self.assertEqual(path, uploaded_path)
        self.assertEqual(
            ("POST", "https://dashscope.example/api/v1/fine-tunes"),
            self.json_calls[0][:2],
        )
        body = self.json_calls[0][3]
        self.assertEqual("qwen3.7-plus", body["model"])
        self.assertEqual(
            [{"data_source_type": "file_id", "file_id": "file-qwen-123"}],
            body["training_datasets"],
        )
        self.assertEqual("local-job", body["job_name"])
        self.assertEqual("sft", body["training_type"])

    def test_status_exposes_provider_artifact_and_evaluation_evidence(self) -> None:
        status = self.adapter.get_fine_tuning_job("ft-qwen-123")

        self.assertEqual("completed", status.state)
        self.assertEqual(100, status.progress_percent)
        self.assertEqual("qwen-ft-model", status.artifact_id)
        self.assertEqual({"usage": 8192, "output_cnt": 1}, status.evaluation)

    def test_cancel_maps_successful_provider_response_to_cancelled(self) -> None:
        status = self.adapter.cancel_fine_tuning_job("ft-qwen-123")

        self.assertEqual("cancelled", status.state)
        self.assertEqual(
            ("POST", "https://dashscope.example/api/v1/fine-tunes/ft-qwen-123/cancel"),
            self.json_calls[-1][:2],
        )

    def test_recover_matches_the_local_job_name_and_rejects_incomplete_listing(self) -> None:
        recovered = self.adapter.recover_fine_tuning_submission("local-job")
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual("ft-qwen-123", recovered.provider_job_id)

        def incomplete(method, url, headers, body, timeout_seconds):
            return {"output": {"total": 1001, "jobs": []}}

        incomplete_adapter = QwenFineTuningAdapter(
            self.adapter.config,
            json_request=incomplete,
            multipart_request=self.adapter.multipart_request,
        )
        with self.assertRaises(AdapterError) as captured:
            incomplete_adapter.recover_fine_tuning_submission("missing-job")
        self.assertEqual("provider_reconciliation_incomplete", captured.exception.code)

    def test_capability_is_explicit_and_unknown_models_are_rejected(self) -> None:
        self.assertTrue(self.adapter.supports_fine_tuning("qwen3.7-plus"))
        self.assertFalse(self.adapter.supports_fine_tuning("qwen3.7-max"))
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            request = self._request(path)
            request = FineTuningRequest(
                provider_id=request.provider_id,
                model_id="qwen3.7-max",
                job_id=request.job_id,
                dataset_path=request.dataset_path,
                dataset_sha256=request.dataset_sha256,
                sample_count=request.sample_count,
            )
            with self.assertRaises(AdapterError) as captured:
                self.adapter.submit_fine_tuning(request)
        self.assertEqual("capability_not_supported", captured.exception.code)

    def test_malformed_upload_response_is_a_stable_error(self) -> None:
        def malformed_upload(url, headers, fields, file_field, file_path, timeout_seconds):
            return {"data": {"uploaded_files": []}}

        adapter = QwenFineTuningAdapter(
            self.adapter.config,
            json_request=self.adapter.json_request,
            multipart_request=malformed_upload,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(AdapterError) as captured:
                adapter.submit_fine_tuning(self._request(path))
        self.assertEqual("invalid_provider_response", captured.exception.code)

    def test_failed_job_creation_deletes_the_uploaded_training_file(self) -> None:
        calls: list[tuple[str, str]] = []

        def failed_create(method, url, headers, body, timeout_seconds):
            calls.append((method, url))
            if method == "POST" and url.endswith("/fine-tunes"):
                raise AdapterError("provider_http_error", "provider rejected training")
            if method == "DELETE" and url.endswith("/files/file-qwen-123"):
                return {"output": {"status": "success"}}
            raise AssertionError(f"unexpected request: {method} {url}")

        adapter = QwenFineTuningAdapter(
            self.adapter.config,
            json_request=failed_create,
            multipart_request=self.adapter.multipart_request,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(AdapterError) as captured:
                adapter.submit_fine_tuning(self._request(path))

        self.assertEqual("provider_http_error", captured.exception.code)
        self.assertEqual(
            [
                ("POST", "https://dashscope.example/api/v1/fine-tunes"),
                ("DELETE", "https://dashscope.example/api/v1/files/file-qwen-123"),
            ],
            calls,
        )


if __name__ == "__main__":
    unittest.main()
