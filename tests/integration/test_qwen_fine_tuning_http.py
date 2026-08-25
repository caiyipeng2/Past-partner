from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from src.providers.base import FineTuningRequest
from src.providers.qwen_fine_tuning import QwenFineTuningAdapter, QwenFineTuningConfig


class _QwenFakeHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, bytes, dict[str, str]]] = []
    job = {
        "job_id": "ft-local-1",
        "job_name": "local-job",
        "status": "SUCCEEDED",
        "finetuned_output": "qwen-local-model",
        "usage": 12,
        "output_cnt": 1,
    }

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler hook
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        _QwenFakeHandler.requests.append(
            (self.path, body, {key: value for key, value in self.headers.items()})
        )
        if self.path == "/api/v1/files":
            self._json({"data": {"uploaded_files": [{"file_id": "file-local-1"}]}})
            return
        if self.path == "/api/v1/fine-tunes":
            payload = json.loads(body.decode("utf-8"))
            self.assert_field(payload, "job_name", "local-job")
            self.assert_field(payload, "training_datasets", [{"data_source_type": "file_id", "file_id": "file-local-1"}])
            self._json({"output": _QwenFakeHandler.job})
            return
        if self.path == "/api/v1/fine-tunes/ft-local-1/cancel":
            self._json({"output": {"status": "success"}})
            return
        self.send_error(404)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler hook
        _QwenFakeHandler.requests.append((self.path, b"", {key: value for key, value in self.headers.items()}))
        if self.path == "/api/v1/fine-tunes?page_no=1&page_size=1000":
            self._json({"output": {"total": 1, "jobs": [_QwenFakeHandler.job]}})
            return
        if self.path == "/api/v1/fine-tunes/ft-local-1":
            self._json({"output": _QwenFakeHandler.job})
            return
        self.send_error(404)

    def _json(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    @staticmethod
    def assert_field(payload: dict[str, object], key: str, expected: object) -> None:
        if payload.get(key) != expected:
            raise AssertionError(f"unexpected {key}: {payload.get(key)!r}")

    def log_message(self, format: str, *args: object) -> None:
        return


class QwenFineTuningHttpIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        _QwenFakeHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _QwenFakeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        base_url = f"http://127.0.0.1:{self.server.server_port}/api/v1"
        self.adapter = QwenFineTuningAdapter(
            QwenFineTuningConfig(
                provider_id="qwen",
                base_url=base_url,
                chat_base_url=base_url,
                api_key="integration-secret",
                allowed_models=frozenset({"qwen3.7-plus"}),
                fine_tuning_models=frozenset({"qwen3.7-plus"}),
                timeout_seconds=5,
            )
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def test_default_http_transport_runs_submit_recover_status_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dataset.jsonl"
            path.write_text('{"messages": [{"role": "assistant", "content": "ok"}]}\n', encoding="utf-8")
            request = FineTuningRequest(
                provider_id="qwen",
                model_id="qwen3.7-plus",
                job_id="local-job",
                dataset_path=path,
                dataset_sha256="a" * 64,
                sample_count=1,
            )
            submission = self.adapter.submit_fine_tuning(request)

        self.assertEqual("ft-local-1", submission.provider_job_id)
        self.assertEqual("ft-local-1", self.adapter.recover_fine_tuning_submission("local-job").provider_job_id)
        status = self.adapter.get_fine_tuning_job("ft-local-1")
        self.assertEqual("completed", status.state)
        self.assertEqual("qwen-local-model", status.artifact_id)
        self.assertEqual({"usage": 12, "output_cnt": 1}, status.evaluation)
        self.assertEqual("cancelled", self.adapter.cancel_fine_tuning_job("ft-local-1").state)

        upload_path, upload_body, upload_headers = _QwenFakeHandler.requests[0]
        self.assertEqual("/api/v1/files", upload_path)
        self.assertIn(b"name=\"purpose\"", upload_body)
        self.assertIn(b"fine-tune", upload_body)
        self.assertIn(b'"messages"', upload_body)
        self.assertEqual("Bearer integration-secret", upload_headers["Authorization"])


if __name__ == "__main__":
    unittest.main()
