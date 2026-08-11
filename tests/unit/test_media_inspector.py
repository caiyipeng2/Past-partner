from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.preprocessing.media_inspector import MediaInspectionError, MediaInspector


class MediaInspectorTests(unittest.TestCase):
    def test_inspects_a_png_from_its_bytes_not_its_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "misleading-name.bin"
            Image.new("RGB", (2, 3), color="white").save(source, format="PNG")

            inspection = MediaInspector().inspect(source, "image/png")

        self.assertEqual("image", inspection["kind"])
        self.assertEqual("image/png", inspection["detected_media_type"])
        self.assertEqual("PNG", inspection["format"])
        self.assertEqual({"width": 2, "height": 3}, inspection["dimensions"])
        self.assertFalse(inspection["provider_transfer"])

    def test_inspects_audio_with_a_local_probe_result(self) -> None:
        probe_result = {
            "format": {"format_name": "ogg", "duration": "3.703583", "size": "153301"},
            "streams": [
                {"codec_name": "vorbis", "codec_type": "audio", "sample_rate": "44100"}
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "audio-without-extension"
            source.write_bytes(b"fixture")

            inspection = MediaInspector(av_probe=lambda _: probe_result).inspect(source, "audio/ogg")

        self.assertEqual("audio", inspection["kind"])
        self.assertEqual("audio/ogg", inspection["detected_media_type"])
        self.assertEqual(3.703583, inspection["duration_seconds"])
        self.assertEqual({"codec": "vorbis", "sample_rate_hz": 44100}, inspection["audio"])
        self.assertFalse(inspection["provider_transfer"])

    def test_inspects_video_and_its_audio_stream_with_a_local_probe_result(self) -> None:
        probe_result = {
            "format": {"format_name": "matroska,webm", "duration": "13.021", "size": "235171"},
            "streams": [
                {"codec_name": "vp9", "codec_type": "video", "width": 3840, "height": 2160},
                {"codec_name": "opus", "codec_type": "audio", "sample_rate": "48000"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "video-without-extension"
            source.write_bytes(b"fixture")

            inspection = MediaInspector(av_probe=lambda _: probe_result).inspect(source, "video/webm")

        self.assertEqual("video", inspection["kind"])
        self.assertEqual("video/webm", inspection["detected_media_type"])
        self.assertEqual(13.021, inspection["duration_seconds"])
        self.assertEqual({"codec": "vp9", "width": 3840, "height": 2160}, inspection["video"])
        self.assertEqual({"codec": "opus", "sample_rate_hz": 48000}, inspection["audio"])

    def test_rejects_declared_audio_when_probe_reports_a_video_stream(self) -> None:
        probe_result = {
            "format": {"format_name": "matroska,webm", "duration": "13.021", "size": "235171"},
            "streams": [{"codec_name": "vp9", "codec_type": "video", "width": 3840, "height": 2160}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "wrong-declaration.bin"
            source.write_bytes(b"fixture")

            with self.assertRaises(MediaInspectionError) as caught:
                MediaInspector(av_probe=lambda _: probe_result).inspect(source, "audio/ogg")

        self.assertEqual("media_type_mismatch", caught.exception.code)

    def test_rejects_non_media_declarations_without_reading_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "text.bin"
            source.write_bytes(b"fixture")

            with self.assertRaises(MediaInspectionError) as caught:
                MediaInspector().inspect(source, "text/plain")

        self.assertEqual("unsupported_media_type", caught.exception.code)

    def test_rejects_a_png_that_fails_full_integrity_verification(self) -> None:
        corrupt_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL7dgAAAABJRU5ErkJggg=="
        )
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "corrupt.png"
            source.write_bytes(corrupt_png)

            with self.assertRaises(MediaInspectionError) as caught:
                MediaInspector().inspect(source, "image/png")

        self.assertEqual("media_metadata_invalid", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
