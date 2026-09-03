"""Consent-gated handoff of bounded uploaded media to a provider."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import math
import os
from pathlib import Path
import shutil
from typing import Any, Iterator, Mapping
from uuid import uuid4

from src.domain.consents import ConsentValidationError
from src.providers.base import MediaAnalysisRequest, MediaAnalysisResult
from src.providers.gateway import ProviderError, ProviderGateway
from src.services.consent_service import ConsentNotFoundError
from src.services.import_repository import ImportRepositoryError
from src.services.import_service import ImportFile, ImportNotFoundError, ImportState
from src.services.storage import StorageLayout
from src.services.upload_service import UploadError, UploadService


DEFAULT_MAX_MEDIA_ANALYSIS_BYTES = 32 * 1024**2
MAX_ANALYSIS_PROMPT_CHARACTERS = 4_096
MAX_DESCRIPTION_CHARACTERS = 8_192
MAX_USAGE_FIELDS = 32
MAX_USAGE_VALUE = 1_000_000_000_000
MAX_PROVIDER_REQUEST_ID_CHARACTERS = 256

_MEDIA_ALIASES = {
    "image": "image",
    "photo": "image",
    "picture": "image",
    "vision": "image",
    "audio": "audio",
    "voice": "audio",
    "sound": "audio",
    "video": "video",
}


class MediaAnalysisError(ValueError):
    """Stable user-safe failure for the media-analysis service boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class MediaAnalysisService:
    """Materialize one approved media file only for the duration of analysis."""

    def __init__(
        self,
        storage: StorageLayout,
        uploads: UploadService,
        consent_gate: Any,
        gateway: ProviderGateway,
        *,
        max_media_bytes: int = DEFAULT_MAX_MEDIA_ANALYSIS_BYTES,
    ) -> None:
        if isinstance(max_media_bytes, bool) or not isinstance(max_media_bytes, int) or max_media_bytes <= 0:
            raise ValueError("max_media_bytes must be a positive integer")
        self.storage = storage
        self.uploads = uploads
        self.consent_gate = consent_gate
        self.gateway = gateway
        self.max_media_bytes = max_media_bytes

    def analyze(
        self,
        owner_id: str,
        import_id: str,
        *,
        consent_id: str,
        provider_id: str,
        model_id: str,
        data_category: str,
        authorization_scope: str,
        prompt: str,
        analysis_kind: str = "description",
        file_id: str | None = None,
    ) -> dict[str, Any]:
        job = self._get_job(owner_id, import_id)
        if job.state is not ImportState.UPLOADED:
            raise MediaAnalysisError(
                "media_analysis_unavailable",
                "media analysis requires a completed uploaded import",
            )

        category = _category(data_category)
        operation = _analysis_kind(analysis_kind)
        if operation == "ocr" and category != "image":
            raise MediaAnalysisError("unsupported_media_operation", "OCR requires image media")
        file_spec, file_offset = self._select_file(job, file_id)
        declared_category = _mime_category(file_spec.media_type)
        if declared_category != category:
            raise MediaAnalysisError(
                "media_type_mismatch",
                "requested media category does not match the uploaded file",
            )
        if file_spec.total_bytes > self.max_media_bytes:
            raise MediaAnalysisError(
                "media_too_large",
                "media exceeds the configured analysis size limit",
            )

        try:
            authorization = self.consent_gate.authorize(
                owner_id,
                consent_id,
                provider_id=provider_id,
                model_id=model_id,
                data_category=category,
                authorization_scope=authorization_scope,
                analysis_kind=operation,
            )
        except ConsentNotFoundError as exc:
            raise MediaAnalysisError("consent_not_found", "media analysis consent was not found") from exc
        except ConsentValidationError as exc:
            raise MediaAnalysisError(exc.code, str(exc)) from exc

        if authorization.persona_id != job.persona_id:
            raise MediaAnalysisError(
                "import_persona_mismatch",
                "consent and import do not belong to the same persona",
            )
        if not getattr(authorization, "authorized", True):
            raise MediaAnalysisError("consent_not_authorized", "media analysis consent was not authorized")

        clean_prompt = _prompt(prompt)
        destination = self.storage.object_path("media-analysis", uuid4().hex, ".bin")
        with self._temporary_destination(destination, file_spec.total_bytes):
            self._materialize_payload(
                owner_id,
                import_id,
                job_total_bytes=int(job.total_bytes),
                file_offset=file_offset,
                file_size=file_spec.total_bytes,
                destination=destination,
            )
            request = MediaAnalysisRequest(
                provider_id=provider_id,
                model_id=model_id,
                media_type=file_spec.media_type,
                media_path=destination,
                prompt=clean_prompt,
                analysis_kind=operation,
            )
            with self._provider_handoff_guard():
                try:
                    # Recheck immediately before the provider sees the temporary
                    # plaintext. ConsentService serializes this check with revoke().
                    final_authorization = self.consent_gate.authorize(
                        owner_id,
                        consent_id,
                        provider_id=provider_id,
                        model_id=model_id,
                        data_category=category,
                        authorization_scope=authorization_scope,
                        analysis_kind=operation,
                    )
                except ConsentNotFoundError as exc:
                    raise MediaAnalysisError("consent_not_found", "media analysis consent was not found") from exc
                except ConsentValidationError as exc:
                    raise MediaAnalysisError(exc.code, str(exc)) from exc
                if final_authorization.persona_id != job.persona_id:
                    raise MediaAnalysisError(
                        "import_persona_mismatch",
                        "consent and import do not belong to the same persona",
                    )
                if not getattr(final_authorization, "authorized", True):
                    raise MediaAnalysisError("consent_not_authorized", "media analysis consent was not authorized")
                try:
                    result = self.gateway.analyze_media(request)
                except ProviderError:
                    raise
                except (AttributeError, TypeError) as exc:
                    raise MediaAnalysisError(
                        "provider_unavailable",
                        "media provider could not complete analysis",
                    ) from exc

        return self._normalize_result(result, category, operation, import_id, file_spec.file_id)

    @contextmanager
    def _provider_handoff_guard(self) -> Iterator[None]:
        consents = getattr(self.consent_gate, "consents", None)
        guard_factory = getattr(consents, "provider_handoff_guard", None)
        if callable(guard_factory):
            with guard_factory():
                yield
            return
        # Lightweight test gates and future implementations may already provide
        # their own serialization; keep the service usable without this optional
        # lifecycle hook.
        with nullcontext():
            yield

    def _get_job(self, owner_id: str, import_id: str):
        try:
            return self.uploads.imports.get(owner_id, import_id)
        except ImportNotFoundError as exc:
            raise MediaAnalysisError("import_not_found", "media import was not found") from exc
        except (ImportRepositoryError, LookupError) as exc:
            raise MediaAnalysisError("import_unavailable", "media import metadata is unavailable") from exc

    @staticmethod
    def _select_file(job: Any, file_id: str | None) -> tuple[ImportFile, int]:
        raw_files = tuple(job.files) if getattr(job, "files", ()) else ()
        if not raw_files:
            raw_files = (
                ImportFile.create(
                    file_id="legacy-file",
                    source_name=job.source_name,
                    media_type=job.media_type,
                    total_bytes=job.total_bytes,
                ),
            )
        selected_index = 0
        if file_id is not None:
            for index, item in enumerate(raw_files):
                if item.file_id == file_id:
                    selected_index = index
                    break
            else:
                raise MediaAnalysisError("file_not_found", "requested media file was not found")
        elif len(raw_files) != 1:
            raise MediaAnalysisError(
                "file_selection_required",
                "a file_id is required when an import contains multiple files",
            )
        offset = sum(item.total_bytes for item in raw_files[:selected_index])
        return raw_files[selected_index], offset

    def _materialize_payload(
        self,
        owner_id: str,
        import_id: str,
        *,
        job_total_bytes: int,
        file_offset: int,
        file_size: int,
        destination: Path,
    ) -> None:
        payload = None
        seen = 0
        target_end = file_offset + file_size
        try:
            payload = self.uploads.iter_payload(owner_id, import_id)
            with destination.open("xb") as output:
                for block in payload:
                    if not isinstance(block, bytes):
                        raise MediaAnalysisError("payload_corrupt", "uploaded media payload is invalid")
                    block_start = seen
                    seen += len(block)
                    if seen > job_total_bytes:
                        raise MediaAnalysisError("payload_corrupt", "uploaded media payload has trailing bytes")
                    write_start = max(file_offset, block_start)
                    write_end = min(target_end, seen)
                    if write_start < write_end:
                        output.write(block[write_start - block_start : write_end - block_start])
                if seen != job_total_bytes:
                    raise MediaAnalysisError("payload_corrupt", "uploaded media payload is incomplete")
                output.flush()
                os.fsync(output.fileno())
        except UploadError as exc:
            raise MediaAnalysisError(exc.code, str(exc)) from exc
        except OSError as exc:
            raise MediaAnalysisError(
                "media_analysis_storage_unavailable",
                "temporary media analysis storage is unavailable",
            ) from exc
        finally:
            close = getattr(payload, "close", None)
            if callable(close):
                close()

    @contextmanager
    def _temporary_destination(self, destination: Path, required_bytes: int) -> Iterator[None]:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(destination.parent).free < required_bytes:
                raise MediaAnalysisError(
                    "media_analysis_storage_unavailable",
                    "temporary media analysis storage is insufficient",
                )
        except OSError as exc:
            raise MediaAnalysisError(
                "media_analysis_storage_unavailable",
                "temporary media analysis storage is unavailable",
            ) from exc
        try:
            yield
        finally:
            try:
                destination.unlink(missing_ok=True)
            except OSError as exc:
                raise MediaAnalysisError(
                    "media_analysis_cleanup_failed",
                    "temporary media analysis data could not be removed",
                ) from exc

    @staticmethod
    def _normalize_result(
        result: MediaAnalysisResult,
        category: str,
        operation: str,
        import_id: str,
        file_id: str,
    ) -> dict[str, Any]:
        if not isinstance(result, MediaAnalysisResult):
            raise MediaAnalysisError("invalid_provider_result", "media provider returned an invalid result")
        description = result.description
        if not isinstance(description, str) or not description.strip():
            raise MediaAnalysisError("invalid_provider_result", "media provider returned no description")
        description = description.strip()[:MAX_DESCRIPTION_CHARACTERS]
        normalized = {
            "import_id": import_id,
            "state": ImportState.UPLOADED.value,
            "provider_id": result.provider_id,
            "model_id": result.model_id,
            "file_id": file_id,
            "media_category": category,
            "analysis_kind": operation,
            "media_type": result.media_type,
            "description": description,
            "usage": _usage(result.usage),
            "provider_transfer": True,
            **_provider_request_id(result.provider_request_id),
        }
        structured_data = _structured_data(result.structured_data, operation)
        if structured_data is not None:
            normalized["structured_data"] = structured_data
        return normalized


def _category(value: object) -> str:
    if not isinstance(value, str):
        raise MediaAnalysisError("unsupported_media_category", "data category is not supported")
    category = _MEDIA_ALIASES.get(value.strip().casefold())
    if category is None:
        raise MediaAnalysisError("unsupported_media_category", "data category is not supported")
    return category


def _analysis_kind(value: object) -> str:
    if not isinstance(value, str):
        raise MediaAnalysisError("unsupported_media_operation", "media analysis operation is not supported")
    operation = value.strip().casefold()
    if operation in {"description", "describe"}:
        return "description"
    if operation == "ocr":
        return operation
    raise MediaAnalysisError("unsupported_media_operation", "media analysis operation is not supported")


def _mime_category(value: object) -> str:
    if not isinstance(value, str) or "/" not in value:
        raise MediaAnalysisError("unsupported_media_category", "uploaded media type is not supported")
    category = value.strip().casefold().split("/", 1)[0]
    if category not in {"image", "audio", "video"}:
        raise MediaAnalysisError("unsupported_media_category", "uploaded media type is not supported")
    return category


def _prompt(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MediaAnalysisError("invalid_prompt", "analysis prompt is required")
    prompt = value.strip()
    if len(prompt) > MAX_ANALYSIS_PROMPT_CHARACTERS:
        raise MediaAnalysisError("invalid_prompt", "analysis prompt is too long")
    return prompt


def _structured_data(value: object, operation: str) -> dict[str, Any] | None:
    if operation != "ocr":
        return None
    if not isinstance(value, Mapping):
        raise MediaAnalysisError("invalid_provider_result", "media provider OCR result is invalid")
    text = value.get("text")
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > MAX_DESCRIPTION_CHARACTERS:
        raise MediaAnalysisError("invalid_provider_result", "media provider OCR text is invalid")
    raw_blocks = value.get("blocks", [])
    if not isinstance(raw_blocks, list) or len(raw_blocks) > 256:
        raise MediaAnalysisError("invalid_provider_result", "media provider OCR blocks are invalid")
    blocks: list[dict[str, Any]] = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, Mapping):
            raise MediaAnalysisError("invalid_provider_result", "media provider OCR blocks are invalid")
        block_text = raw_block.get("text")
        if not isinstance(block_text, str) or not block_text.strip() or len(block_text.strip()) > 2_048:
            raise MediaAnalysisError("invalid_provider_result", "media provider OCR block text is invalid")
        block: dict[str, Any] = {"text": block_text.strip()}
        if "confidence" in raw_block:
            confidence = raw_block["confidence"]
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise MediaAnalysisError("invalid_provider_result", "media provider OCR confidence is invalid")
            confidence_value = float(confidence)
            if not math.isfinite(confidence_value) or not 0 <= confidence_value <= 1:
                raise MediaAnalysisError("invalid_provider_result", "media provider OCR confidence is invalid")
            block["confidence"] = confidence_value
        if "bbox" in raw_block:
            bbox = raw_block["bbox"]
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise MediaAnalysisError("invalid_provider_result", "media provider OCR bounding box is invalid")
            normalized_bbox: list[float] = []
            for coordinate in bbox:
                if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                    raise MediaAnalysisError("invalid_provider_result", "media provider OCR bounding box is invalid")
                coordinate_value = float(coordinate)
                if not math.isfinite(coordinate_value) or not 0 <= coordinate_value <= 1:
                    raise MediaAnalysisError("invalid_provider_result", "media provider OCR bounding box is invalid")
                normalized_bbox.append(coordinate_value)
            block["bbox"] = normalized_bbox
        blocks.append(block)
    return {"text": text.strip(), "blocks": blocks}


def _usage(value: object) -> dict[str, int] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise MediaAnalysisError("invalid_provider_result", "media provider usage is invalid")
    if len(value) > MAX_USAGE_FIELDS:
        raise MediaAnalysisError("invalid_provider_result", "media provider usage is invalid")
    normalized: dict[str, int] = {}
    for key, item in value.items():
        if (
            not isinstance(key, str)
            or not key.strip()
            or len(key) > 64
            or isinstance(item, bool)
            or not isinstance(item, int)
            or item < 0
            or item > MAX_USAGE_VALUE
        ):
            raise MediaAnalysisError("invalid_provider_result", "media provider usage is invalid")
        normalized[key.strip()] = item
    return normalized


def _provider_request_id(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_PROVIDER_REQUEST_ID_CHARACTERS:
        raise MediaAnalysisError("invalid_provider_result", "media provider request id is invalid")
    return {"provider_request_id": value.strip()}


__all__ = ["DEFAULT_MAX_MEDIA_ANALYSIS_BYTES", "MediaAnalysisError", "MediaAnalysisService"]
