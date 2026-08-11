"""Local, metadata-only inspection for completed media imports."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from PIL import Image, UnidentifiedImageError
except ModuleNotFoundError:
    Image = None
    UnidentifiedImageError = OSError


MAX_IMAGE_PIXELS = 100_000_000
_IMAGE_MEDIA_TYPES = {
    "BMP": "image/bmp",
    "GIF": "image/gif",
    "ICO": "image/x-icon",
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "TIFF": "image/tiff",
    "WEBP": "image/webp",
}
_DECLARED_ALIASES = {
    "audio/x-wav": "audio/wav",
    "image/jpg": "image/jpeg",
}


class MediaInspectionError(ValueError):
    """Stable, user-safe failure for local metadata inspection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MediaInspector:
    """Inspect media bytes without retaining them or contacting a provider."""

    def __init__(self, av_probe: Callable[[Path], Mapping[str, Any]] | None = None) -> None:
        self._av_probe = av_probe or _ffprobe

    def inspect(self, source: Path, declared_media_type: str) -> dict[str, Any]:
        if not source.is_file():
            raise MediaInspectionError("media_metadata_invalid", "media source is unavailable")
        category = _declared_category(declared_media_type)
        if category == "image":
            result = self._inspect_image(source, declared_media_type)
        elif category in {"audio", "video"}:
            result = self._inspect_av(source, declared_media_type, category)
        else:
            raise MediaInspectionError(
                "unsupported_media_type", "media type is not supported for inspection"
            )
        return {**result, "size_bytes": source.stat().st_size, "provider_transfer": False}

    @staticmethod
    def _inspect_image(source: Path, declared_media_type: str) -> dict[str, Any]:
        if Image is None:
            raise MediaInspectionError(
                "media_processor_unavailable",
                "image inspection requires the optional parser dependencies",
            )
        try:
            with Image.open(source) as image:
                format_name = image.format
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise MediaInspectionError("media_metadata_invalid", "image dimensions are unsafe")
                # verify() validates the encoded image while avoiding a decoded pixel buffer.
                image.verify()
        except MediaInspectionError:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            raise MediaInspectionError("media_metadata_invalid", "image bytes are not valid") from exc

        if not isinstance(format_name, str) or format_name not in _IMAGE_MEDIA_TYPES:
            raise MediaInspectionError("media_metadata_invalid", "image format is not supported")
        detected_media_type = _IMAGE_MEDIA_TYPES[format_name]
        _require_matching_media_type(declared_media_type, detected_media_type)
        return {
            "kind": "image",
            "detected_media_type": detected_media_type,
            "format": format_name,
            "dimensions": {"width": width, "height": height},
        }

    def _inspect_av(
        self,
        source: Path,
        declared_media_type: str,
        declared_category: str,
    ) -> dict[str, Any]:
        probe_result = self._av_probe(source)
        if not isinstance(probe_result, Mapping):
            raise MediaInspectionError("media_metadata_invalid", "media probe returned invalid metadata")
        format_info = probe_result.get("format")
        streams = probe_result.get("streams")
        if not isinstance(format_info, Mapping) or not isinstance(streams, list):
            raise MediaInspectionError("media_metadata_invalid", "media probe returned incomplete metadata")

        format_names = _format_names(format_info.get("format_name"))
        duration_seconds = _duration(format_info.get("duration"))
        audio_stream = _first_stream(streams, "audio")
        video_stream = _first_stream(streams, "video")
        if declared_category == "audio":
            if audio_stream is None or video_stream is not None:
                raise MediaInspectionError(
                    "media_type_mismatch", "declared audio does not match inspected media"
                )
        elif video_stream is None:
            raise MediaInspectionError(
                "media_type_mismatch", "declared video does not match inspected media"
            )

        detected_media_type = _av_media_type(declared_category, format_names)
        _require_matching_media_type(declared_media_type, detected_media_type)
        result: dict[str, Any] = {
            "kind": declared_category,
            "detected_media_type": detected_media_type,
            "format": _preferred_format(format_names),
            "duration_seconds": duration_seconds,
        }
        if video_stream is not None:
            result["video"] = _video_metadata(video_stream)
        if audio_stream is not None:
            result["audio"] = _audio_metadata(audio_stream)
        return result


def _ffprobe(source: Path) -> Mapping[str, Any]:
    executable = shutil.which("ffprobe")
    if executable is None:
        raise MediaInspectionError(
            "media_processor_unavailable", "audio and video inspection requires ffprobe"
        )
    try:
        result = subprocess.run(
            [
                executable,
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration,size:stream=codec_type,codec_name,sample_rate,width,height",
                "-of",
                "json",
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaInspectionError(
            "media_processor_unavailable", "audio and video inspection could not start"
        ) from exc
    if result.returncode != 0:
        raise MediaInspectionError("media_metadata_invalid", "media bytes are not valid")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise MediaInspectionError("media_metadata_invalid", "media probe returned invalid metadata") from exc
    if not isinstance(parsed, Mapping):
        raise MediaInspectionError("media_metadata_invalid", "media probe returned invalid metadata")
    return parsed


def _declared_category(value: object) -> str:
    if not isinstance(value, str) or "/" not in value:
        return ""
    return value.strip().lower().partition("/")[0]


def _normalized_media_type(value: str) -> str:
    normalized = value.strip().lower()
    return _DECLARED_ALIASES.get(normalized, normalized)


def _require_matching_media_type(declared_media_type: str, detected_media_type: str) -> None:
    if _normalized_media_type(declared_media_type) != detected_media_type:
        raise MediaInspectionError(
            "media_type_mismatch", "declared media type does not match inspected media"
        )


def _format_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise MediaInspectionError("media_metadata_invalid", "media format is missing")
    names = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    if not names:
        raise MediaInspectionError("media_metadata_invalid", "media format is missing")
    return names


def _duration(value: object) -> float:
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaInspectionError("media_metadata_invalid", "media duration is invalid") from exc
    if not math.isfinite(duration) or duration < 0:
        raise MediaInspectionError("media_metadata_invalid", "media duration is invalid")
    return duration


def _first_stream(streams: list[object], kind: str) -> Mapping[str, Any] | None:
    for stream in streams:
        if isinstance(stream, Mapping) and stream.get("codec_type") == kind:
            return stream
    return None


def _av_media_type(category: str, format_names: tuple[str, ...]) -> str:
    names = set(format_names)
    if "webm" in names:
        return f"{category}/webm"
    if "ogg" in names:
        return f"{category}/ogg"
    if category == "audio" and "wav" in names:
        return "audio/wav"
    if category == "audio" and "mp3" in names:
        return "audio/mpeg"
    if category == "video" and "mov" in names and "mp4" in names:
        return "video/mp4"
    raise MediaInspectionError("media_metadata_invalid", "media format is not supported")


def _preferred_format(format_names: tuple[str, ...]) -> str:
    if "webm" in format_names:
        return "webm"
    return format_names[0]


def _audio_metadata(stream: Mapping[str, Any]) -> dict[str, Any]:
    codec = stream.get("codec_name")
    sample_rate = stream.get("sample_rate")
    if not isinstance(codec, str) or not codec:
        raise MediaInspectionError("media_metadata_invalid", "audio codec is invalid")
    try:
        sample_rate_hz = int(sample_rate)
    except (TypeError, ValueError) as exc:
        raise MediaInspectionError("media_metadata_invalid", "audio sample rate is invalid") from exc
    if sample_rate_hz <= 0:
        raise MediaInspectionError("media_metadata_invalid", "audio sample rate is invalid")
    return {"codec": codec, "sample_rate_hz": sample_rate_hz}


def _video_metadata(stream: Mapping[str, Any]) -> dict[str, Any]:
    codec = stream.get("codec_name")
    width = stream.get("width")
    height = stream.get("height")
    if not isinstance(codec, str) or not codec:
        raise MediaInspectionError("media_metadata_invalid", "video codec is invalid")
    try:
        dimensions = {"width": int(width), "height": int(height)}
    except (TypeError, ValueError) as exc:
        raise MediaInspectionError("media_metadata_invalid", "video dimensions are invalid") from exc
    if dimensions["width"] <= 0 or dimensions["height"] <= 0:
        raise MediaInspectionError("media_metadata_invalid", "video dimensions are invalid")
    return {"codec": codec, **dimensions}
