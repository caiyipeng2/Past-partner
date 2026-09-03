from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import uuid4
from unittest.mock import patch

from src.domain.consents import ConsentValidationError
from src.providers.base import MediaAnalysisRequest, MediaAnalysisResult
from src.providers.gateway import ProviderError
from src.services.import_service import ImportFile, ImportState
from src.services.storage import StorageLayout


class _FakeUploads:
    def __init__(self, root: Path, *, payload: bytes = b"image-bytes") -> None:
        self.storage = StorageLayout(root)
        self.payload = payload
        self.payload_reads = 0
        self.job = SimpleNamespace(
            id="import-1",
            persona_id="persona-1",
            media_type="image/png",
            total_bytes=len(payload),
            state=ImportState.UPLOADED,
            files=(
                ImportFile.create(
                    file_id="file-1",
                    source_name="photo.png",
                    media_type="image/png",
                    total_bytes=len(payload),
                ),
            ),
        )
        self.imports = SimpleNamespace(get=self._get)

    def _get(self, owner_id: str, import_id: str):
        if owner_id != "owner-1" or import_id != self.job.id:
            raise LookupError("import not found")
        return self.job

    def iter_payload(self, owner_id: str, import_id: str):
        self.payload_reads += 1

        def iterator():
            yield self.payload[:3]
            yield self.payload[3:]

        return iterator()


class _FakeConsentGate:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.error: Exception | None = None

    def authorize(self, owner_id: str, consent_id: str, **scope):
        self.calls.append((owner_id, consent_id, scope))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            authorized=True,
            consent_id=consent_id,
            persona_id="persona-1",
            provider_id=scope["provider_id"],
            model_id=scope["model_id"],
            data_category=scope["data_category"],
            authorization_scope=scope["authorization_scope"],
            required_capability="vision",
        )


class _FakeGateway:
    def __init__(self) -> None:
        self.requests: list[MediaAnalysisRequest] = []
        self.observed_payloads: list[bytes] = []
        self.error: Exception | None = None
        self.description = "一张测试图片"

    def analyze_media(self, request: MediaAnalysisRequest) -> MediaAnalysisResult:
        self.requests.append(request)
        self.observed_payloads.append(request.media_path.read_bytes())
        if self.error is not None:
            raise self.error
        return MediaAnalysisResult(
            provider_id=request.provider_id,
            model_id=request.model_id,
            media_type=request.media_type,
            description=self.description,
            usage={"media_units": 1},
            provider_request_id="request-1",
        )


class MediaAnalysisServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix=f"past-partner-media-analysis-{uuid4().hex}-"))
        self.uploads = _FakeUploads(self.root)
        self.consent_gate = _FakeConsentGate()
        self.gateway = _FakeGateway()
        from src.services.media_analysis_service import MediaAnalysisService

        self.service = MediaAnalysisService(
            storage=self.uploads.storage,
            uploads=self.uploads,
            consent_gate=self.consent_gate,
            gateway=self.gateway,
            max_media_bytes=1024,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _analyze(self, **overrides):
        values = {
            "owner_id": "owner-1",
            "import_id": "import-1",
            "consent_id": "consent-1",
            "provider_id": "test",
            "model_id": "deterministic-vision",
            "data_category": "image",
            "authorization_scope": "persona-image-analysis",
            "prompt": "描述图片",
        }
        values.update(overrides)
        return self.service.analyze(**values)

    def test_authorizes_before_any_payload_read(self) -> None:
        self.consent_gate.error = ConsentValidationError("consent_revoked", "consent is revoked")

        with self.assertRaisesRegex(Exception, "consent is revoked") as captured:
            self._analyze()

        self.assertEqual("consent_revoked", captured.exception.code)
        self.assertEqual(0, self.uploads.payload_reads)
        self.assertEqual([], self.gateway.requests)

    def test_materializes_selected_payload_and_cleans_plaintext_after_success(self) -> None:
        result = self._analyze()

        self.assertEqual("一张测试图片", result["description"])
        self.assertEqual("image", result["media_category"])
        self.assertTrue(result["provider_transfer"])
        self.assertEqual({"media_units": 1}, result["usage"])
        self.assertNotIn("media_path", result)
        self.assertNotIn(self.uploads.payload.decode(), str(result))
        self.assertEqual(1, self.uploads.payload_reads)
        self.assertEqual(1, len(self.gateway.requests))
        request = self.gateway.requests[0]
        self.assertEqual([self.uploads.payload], self.gateway.observed_payloads)
        self.assertFalse(request.media_path.exists())

    def test_rejects_payload_over_limit_before_reading_it(self) -> None:
        self.uploads.job.total_bytes = 2048
        self.uploads.job.files = (
            ImportFile.create(
                file_id="file-1",
                source_name="photo.png",
                media_type="image/png",
                total_bytes=2048,
            ),
        )

        with self.assertRaises(Exception) as captured:
            self._analyze()

        self.assertEqual("media_too_large", captured.exception.code)
        self.assertEqual(0, self.uploads.payload_reads)
        self.assertEqual([], self.gateway.requests)

    def test_materializes_only_the_selected_file_from_a_multi_file_import(self) -> None:
        self.uploads.payload = b"onetwo"
        self.uploads.job.total_bytes = len(self.uploads.payload)
        self.uploads.job.files = (
            ImportFile.create(
                file_id="file-1",
                source_name="one.png",
                media_type="image/png",
                total_bytes=3,
            ),
            ImportFile.create(
                file_id="file-2",
                source_name="two.png",
                media_type="image/png",
                total_bytes=4,
            ),
        )

        self._analyze(file_id="file-2")

        self.assertEqual([b"two"], self.gateway.observed_payloads)

        with self.assertRaises(Exception) as missing_selection:
            self._analyze(file_id=None)
        self.assertEqual("file_selection_required", missing_selection.exception.code)

    def test_rejects_non_uploaded_import_before_provider_handoff(self) -> None:
        self.uploads.job.state = ImportState.PROCESSING

        with self.assertRaises(Exception) as captured:
            self._analyze()

        self.assertEqual("media_analysis_unavailable", captured.exception.code)
        self.assertEqual(0, self.uploads.payload_reads)
        self.assertEqual([], self.gateway.requests)

    def test_maps_storage_and_provider_failures_and_still_cleans_temp_file(self) -> None:
        with patch(
            "src.services.media_analysis_service.shutil.disk_usage",
            return_value=SimpleNamespace(free=0),
        ):
            with self.assertRaises(Exception) as storage_error:
                self._analyze()
        self.assertEqual("media_analysis_storage_unavailable", storage_error.exception.code)

        self.gateway.error = ProviderError("provider_unavailable", "provider could not be reached")
        with self.assertRaises(ProviderError) as provider_error:
            self._analyze()
        self.assertEqual("provider_unavailable", provider_error.exception.code)
        self.assertFalse(list((self.root / "media-analysis").glob("*.bin")))

    def test_audio_transcription_reuses_exact_audio_consent_and_normalizes_text(self) -> None:
        self.uploads.job.media_type = "audio/wav"
        self.uploads.job.files = (
            ImportFile.create(
                file_id="file-1",
                source_name="voice.wav",
                media_type="audio/wav",
                total_bytes=len(self.uploads.payload),
            ),
        )
        self.gateway.description = "这是一段音频转写文本"

        result = self._analyze(
            data_category="audio",
            authorization_scope="persona-audio-transcription",
            prompt="请转写音频",
        )

        self.assertEqual("audio", result["media_category"])
        self.assertEqual("这是一段音频转写文本", result["description"])
        self.assertEqual("audio", self.consent_gate.calls[0][2]["data_category"])
        self.assertEqual("persona-audio-transcription", self.consent_gate.calls[0][2]["authorization_scope"])
        self.assertEqual("audio/wav", self.gateway.requests[0].media_type)
        self.assertFalse(self.gateway.requests[0].media_path.exists())


if __name__ == "__main__":
    unittest.main()
