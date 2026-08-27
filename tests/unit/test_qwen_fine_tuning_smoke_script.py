from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import os
import unittest
from unittest.mock import patch

from src.providers.qwen_fine_tuning import QwenFineTuningAdapter, QwenFineTuningConfig


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "qwen_fine_tuning_smoke.py"
SPEC = importlib.util.spec_from_file_location("qwen_fine_tuning_smoke_script", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class QwenFineTuningSmokeScriptTests(unittest.TestCase):
    def test_smoke_is_disabled_without_explicit_opt_in(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, {"PAST_PARTNER_QWEN_FINE_TUNING_SMOKE": ""}, clear=False):
            with contextlib.redirect_stdout(output):
                result = smoke.main([])

        self.assertEqual(2, result)
        self.assertEqual("disabled", json.loads(output.getvalue())["status"])

    def test_success_output_contains_only_bounded_lifecycle_metadata(self) -> None:
        def json_request(method, url, headers, body, timeout_seconds):
            if method == "POST" and url.endswith("/fine-tunes"):
                return {"output": {"job_id": "remote-job"}}
            if method == "GET" and url.endswith("/fine-tunes/remote-job"):
                return {"output": {"status": "RUNNING"}}
            if method == "POST" and url.endswith("/fine-tunes/remote-job/cancel"):
                return {"output": {"status": "success"}}
            raise AssertionError(f"unexpected request {method} {url}")

        adapter = QwenFineTuningAdapter(
            QwenFineTuningConfig(
                provider_id="qwen",
                base_url="https://dashscope.example/api/v1",
                api_key="secret-key",
                allowed_models=frozenset({"qwen3.7-plus"}),
                fine_tuning_models=frozenset({"qwen3.7-plus"}),
            ),
            json_request=json_request,
            multipart_request=lambda *args: {"data": {"uploaded_files": [{"file_id": "file-1"}]}},
        )
        output = io.StringIO()
        with patch.dict(
            os.environ,
            {"PAST_PARTNER_QWEN_FINE_TUNING_SMOKE": "1"},
            clear=False,
        ):
            with patch.object(smoke, "build_provider_adapters", return_value={"qwen": adapter}):
                with contextlib.redirect_stdout(output):
                    result = smoke.main(["--model", "qwen3.7-plus"])

        self.assertEqual(0, result)
        rendered = output.getvalue()
        self.assertNotIn("secret-key", rendered)
        self.assertEqual("cancelled", json.loads(rendered)["state"])

    def test_completed_without_artifact_or_evaluation_is_rejected(self) -> None:
        def json_request(method, url, headers, body, timeout_seconds):
            if method == "POST" and url.endswith("/fine-tunes"):
                return {"output": {"job_id": "remote-job"}}
            if method == "GET" and url.endswith("/fine-tunes/remote-job"):
                return {"output": {"status": "SUCCEEDED"}}
            raise AssertionError(f"unexpected request {method} {url}")

        adapter = QwenFineTuningAdapter(
            QwenFineTuningConfig(
                provider_id="qwen",
                base_url="https://dashscope.example/api/v1",
                api_key="secret-key",
                allowed_models=frozenset({"qwen3.7-plus"}),
                fine_tuning_models=frozenset({"qwen3.7-plus"}),
            ),
            json_request=json_request,
            multipart_request=lambda *args: {"data": {"uploaded_files": [{"file_id": "file-1"}]}},
        )
        output = io.StringIO()
        with patch.dict(os.environ, {"PAST_PARTNER_QWEN_FINE_TUNING_SMOKE": "1"}, clear=False):
            with patch.object(smoke, "build_provider_adapters", return_value={"qwen": adapter}):
                with contextlib.redirect_stdout(output):
                    result = smoke.main(["--model", "qwen3.7-plus"])

        self.assertEqual(1, result)
        payload = json.loads(output.getvalue())
        self.assertEqual("failed", payload["status"])
        self.assertEqual("training_result_unverified", payload["error_code"])


if __name__ == "__main__":
    unittest.main()
