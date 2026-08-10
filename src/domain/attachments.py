"""Normalize attachment references without loading or persisting raw media."""

from __future__ import annotations

import json
import mimetypes
from pathlib import PurePosixPath
import re
from typing import Any, Mapping


class AttachmentValidationError(ValueError):
    """Raised when an attachment reference is unsafe or cannot be normalized."""


_MIME_PATTERN = re.compile(r"^[a-z0-9.+-]+/[a-z0-9.+-]+$", re.IGNORECASE)
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$", re.IGNORECASE)
_RAW_FIELDS = frozenset({"bytes", "content", "data", "blob", "payload", "raw"})
_KIND_ALIASES = {
    "photo": "image",
    "picture": "image",
    "图片": "image",
    "voice": "audio",
    "sound": "audio",
    "语音": "audio",
    "movie": "video",
    "影片": "video",
    "document": "file",
    "附件": "file",
    "sticker": "sticker",
    "emoji": "sticker",
}
_KNOWN_KINDS = frozenset({"image", "audio", "video", "file", "sticker", "unknown"})


def normalize_attachments(
    raw: object,
    *,
    message_type: str = "text",
) -> tuple[dict[str, Any], ...]:
    """Return safe, metadata-only attachment references.

    The importer intentionally keeps paths logical and relative to the selected
    source. It never reads a referenced file and strips raw byte-like fields so
    later consent-aware media processing remains a separate operation.
    """

    if raw is None or raw == "":
        return ()
    if isinstance(raw, Mapping):
        return (_normalize_mapping(raw, message_type=message_type),)
    if isinstance(raw, (list, tuple)):
        result: list[dict[str, Any]] = []
        for item in raw:
            result.extend(normalize_attachments(item, message_type=message_type))
        return tuple(result)
    if isinstance(raw, str):
        return _normalize_string(raw, message_type=message_type)
    raise AttachmentValidationError("attachments must contain objects or references")


def _normalize_string(value: str, *, message_type: str) -> tuple[dict[str, Any], ...]:
    text = value.strip()
    if not text:
        return ()
    if text.startswith("data:"):
        raise AttachmentValidationError("inline data attachments are not accepted")
    if text[0] in "[{":
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            decoded = None
        if decoded is not None:
            return normalize_attachments(decoded, message_type=message_type)
    references = [item.strip() for item in re.split(r"[\r\n;]+", text) if item.strip()]
    return tuple(_normalize_reference(item, message_type=message_type) for item in references)


def _normalize_reference(value: str, *, message_type: str) -> dict[str, Any]:
    parts = [item.strip() for item in value.split("|")]
    mapping: dict[str, Any] = {"path": parts[0]}
    if len(parts) > 1 and parts[1]:
        mapping["media_type"] = parts[1]
    if len(parts) > 2 and parts[2]:
        mapping["size"] = parts[2]
    if len(parts) > 3:
        raise AttachmentValidationError("attachment reference has too many fields")
    return _normalize_mapping(mapping, message_type=message_type)


def _normalize_mapping(value: Mapping[str, Any], *, message_type: str) -> dict[str, Any]:
    if any(isinstance(key, str) and key.casefold() in _RAW_FIELDS for key in value):
        raise AttachmentValidationError("raw attachment bytes are not accepted")

    path = _text(value, "path", "file_path", "filepath", "src", "href", "uri")
    url = _text(value, "url", "media_url")
    name = _text(value, "name", "filename", "file_name", "title", "alt")
    media_type = _text(value, "media_type", "mime", "mime_type", "content_type")
    kind = _text(value, "kind", "category", "attachment_type", "type")
    size = _size(value.get("size", value.get("file_size", value.get("length"))))
    sha256 = _text(value, "sha256", "hash")

    if path and _is_external_url(path):
        url = url or path
        path = None
    elif path:
        path = _safe_relative_path(path)
    if url and not _is_external_url(url):
        path = path or _safe_relative_path(url)
        url = None
    if not path and not url and not name:
        raise AttachmentValidationError("attachment reference requires a path, URL, or name")

    if path and not name:
        name = path.rsplit("/", 1)[-1]
    normalized_media_type = _normalize_media_type(media_type)
    if normalized_media_type is None:
        normalized_media_type = _infer_media_type(name or path or url)
    normalized_kind = _normalize_kind(kind, normalized_media_type, message_type, name or path or url)

    result: dict[str, Any] = {}
    if path:
        result["path"] = path
    if name:
        result["name"] = _safe_name(name)
    if url:
        result["url"] = url
    if normalized_media_type:
        result["media_type"] = normalized_media_type
    result["kind"] = normalized_kind
    if size is not None:
        result["size"] = size
    if sha256 and _SHA256_PATTERN.fullmatch(sha256):
        result["sha256"] = sha256.lower()
    return result


def _text(value: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _size(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise AttachmentValidationError("attachment size must be an integer") from exc
    if result < 0:
        raise AttachmentValidationError("attachment size cannot be negative")
    return result


def _safe_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if not normalized or "\x00" in normalized:
        raise AttachmentValidationError("attachment path is empty or contains NUL")
    if normalized.startswith("/") or re.match(r"^[a-zA-Z]:/", normalized):
        raise AttachmentValidationError("absolute attachment paths are not accepted")
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", normalized):
        raise AttachmentValidationError("unsupported attachment URI scheme")
    parts = [part for part in PurePosixPath(normalized).parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise AttachmentValidationError("attachment path traversal is not accepted")
    return "/".join(parts)


def _safe_name(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    name = normalized.rsplit("/", 1)[-1]
    if not name or name in {".", ".."}:
        raise AttachmentValidationError("attachment name is invalid")
    return name


def _is_external_url(value: str) -> bool:
    return value.casefold().startswith(("http://", "https://"))


def _normalize_media_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.casefold()
    return normalized if _MIME_PATTERN.fullmatch(normalized) else None


def _infer_media_type(value: str | None) -> str | None:
    if not value:
        return None
    guessed, _ = mimetypes.guess_type(value, strict=False)
    return guessed.casefold() if guessed else None


def _normalize_kind(
    value: str | None,
    media_type: str | None,
    message_type: str,
    reference: str,
) -> str:
    if value:
        normalized = _KIND_ALIASES.get(value.casefold(), value.casefold())
        if normalized in _KNOWN_KINDS:
            return normalized
    if media_type:
        prefix = media_type.split("/", 1)[0]
        if prefix in {"image", "audio", "video"}:
            return prefix
        if media_type in {"image/gif", "application/x-sticker"}:
            return "sticker"
    normalized_message_type = message_type.casefold().strip()
    if normalized_message_type in _KNOWN_KINDS:
        return normalized_message_type
    inferred = _KIND_ALIASES.get(normalized_message_type)
    if inferred:
        return inferred
    return "sticker" if "sticker" in reference.casefold() or "emoji" in reference.casefold() else "file"
