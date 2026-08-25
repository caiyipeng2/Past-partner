"""Import job lifecycle and its persona ownership invariant."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum
import re
from collections.abc import Sequence
from typing import Any, Mapping
from uuid import uuid4

from src.services.import_repository import ImportRepository
from src.services.persona_service import PersonaNotFoundError, PersonaService


DEFAULT_MAX_IMPORT_BYTES = 3 * 1024**3
MAX_IMPORT_FILES = 1_024
_FILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


class ImportState(str, Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ImportNotFoundError(LookupError):
    """Raised when an import job identifier is unknown."""


class ImportValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ImportFile:
    file_id: str
    source_name: str
    media_type: str
    total_bytes: int
    sha256: str | None = None

    @classmethod
    def create(
        cls,
        source_name: object,
        media_type: object,
        total_bytes: object,
        sha256: object = None,
        file_id: object = None,
    ) -> "ImportFile":
        return cls(
            file_id=_file_id(file_id),
            source_name=_metadata_text(source_name, "source_name", maximum=512),
            media_type=_metadata_text(media_type, "media_type", maximum=255),
            total_bytes=_total_bytes(total_bytes),
            sha256=_sha256(sha256),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ImportFile":
        if not isinstance(value, Mapping):
            raise ImportValidationError("invalid_file", "each file must be an object")
        try:
            return cls.create(
                source_name=value["source_name"],
                media_type=value["media_type"],
                total_bytes=value["total_bytes"],
                sha256=value.get("sha256"),
                file_id=value["file_id"],
            )
        except KeyError as exc:
            raise ImportValidationError("missing_file_field", f"file missing {exc.args[0]}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "source_name": self.source_name,
            "media_type": self.media_type,
            "total_bytes": self.total_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ImportJob:
    id: str
    persona_id: str
    source_name: str
    media_type: str
    total_bytes: int
    received_bytes: int
    chunk_count: int
    state: ImportState
    created_at: str
    updated_at: str
    files: tuple[ImportFile, ...] = ()
    normalized_at: str | None = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImportJob":
        raw_files = value.get("files")
        files = () if raw_files is None else _stored_files(raw_files)
        total_bytes = int(value["total_bytes"])
        if files and sum(item.total_bytes for item in files) != total_bytes:
            raise ImportValidationError("manifest_total_mismatch", "total_bytes must equal the file size sum")
        return cls(
            id=str(value["id"]),
            persona_id=str(value["persona_id"]),
            source_name=str(value["source_name"]),
            media_type=str(value["media_type"]),
            total_bytes=total_bytes,
            received_bytes=int(value.get("received_bytes", 0)),
            chunk_count=int(value.get("chunk_count", 0)),
            state=ImportState(value["state"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
            files=files,
            normalized_at=(str(value["normalized_at"]) if value.get("normalized_at") is not None else None),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "persona_id": self.persona_id,
            "source_name": self.source_name,
            "media_type": self.media_type,
            "total_bytes": self.total_bytes,
            "received_bytes": self.received_bytes,
            "chunk_count": self.chunk_count,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.files:
            result["files"] = [item.to_dict() for item in self.files]
        if self.normalized_at is not None:
            result["normalized_at"] = self.normalized_at
        return result


class ImportService:
    def __init__(
        self,
        repository: ImportRepository,
        personas: PersonaService,
        max_import_bytes: int = DEFAULT_MAX_IMPORT_BYTES,
    ):
        if max_import_bytes < 0:
            raise ValueError("max_import_bytes must be non-negative")
        self.repository = repository
        self.personas = personas
        self.max_import_bytes = max_import_bytes

    def create(
        self,
        owner_id: str | None = None,
        persona_id: str | None = None,
        source_name: str | int | None = None,
        total_bytes: int | str | None = None,
        media_type: str | None = None,
        *,
        files: Sequence[Mapping[str, Any]] | None = None,
    ) -> ImportJob:
        if files is None and media_type is None:
            media_type = total_bytes
            total_bytes = source_name
            source_name = persona_id
            persona_id = owner_id
            owner_id = None
        if persona_id is None:
            raise TypeError("persona_id is required")
        try:
            self.personas.get(owner_id, persona_id)
        except PersonaNotFoundError as exc:
            raise ImportValidationError("persona_not_found", "select an existing persona") from exc

        import_files = _build_files(files, source_name, media_type, total_bytes)
        aggregate_bytes = sum(item.total_bytes for item in import_files)
        if aggregate_bytes > self.max_import_bytes:
            raise ImportValidationError("import_too_large", "import exceeds the configured size limit")

        clean_source_name = import_files[0].source_name
        clean_media_type = import_files[0].media_type
        now = datetime.now(UTC).isoformat()
        job = ImportJob(
            id=str(uuid4()),
            persona_id=persona_id,
            source_name=clean_source_name,
            media_type=clean_media_type,
            total_bytes=aggregate_bytes,
            received_bytes=0,
            chunk_count=0,
            state=ImportState.CREATED,
            created_at=now,
            updated_at=now,
            files=import_files,
        )
        self.repository.create(owner_id, job)
        return job

    def get(self, owner_id: str, import_id: str | None = None) -> ImportJob:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        job = self.repository.get(owner_id, import_id)
        if job is None:
            raise ImportNotFoundError("import not found")
        return job

    def save(self, owner_id: str | ImportJob, job: ImportJob | None = None) -> None:
        if job is None:
            job = owner_id
            owner_id = None
        self.repository.save(owner_id, job)

    def delete(self, owner_id: str, import_id: str | None = None) -> bool:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        return self.repository.delete(owner_id, import_id)

    def list(self, owner_id: str | None = None) -> list[ImportJob]:
        return self.repository.list(owner_id)

    def list_expired_terminal(self, owner_id: str, cutoff: datetime) -> list[ImportJob]:
        return self.repository.list_expired_terminal(owner_id, cutoff)

    def list_expired_normalized(self, owner_id: str, cutoff: datetime) -> list[ImportJob]:
        return self.repository.list_expired_normalized(owner_id, cutoff)

    def mark_normalized(
        self,
        owner_id: str,
        import_id: str,
        *,
        normalized_at: str | None = None,
    ) -> ImportJob:
        job = self.get(owner_id, import_id)
        timestamp = normalized_at or datetime.now(UTC).isoformat()
        try:
            parsed = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError) as exc:
            raise ImportValidationError("invalid_normalized_at", "normalized_at must be a timestamp") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ImportValidationError("invalid_normalized_at", "normalized_at must include a timezone")
        if job.normalized_at is not None:
            return job
        canonical = parsed.astimezone(UTC).isoformat()
        updated = replace(job, normalized_at=canonical, updated_at=canonical)
        self.save(owner_id, updated)
        return updated

    def list_for_persona(self, owner_id: str, persona_id: str) -> list[ImportJob]:
        return self.repository.list_for_persona(owner_id, persona_id)

    def get_manifest(self, owner_id: str, import_id: str | None = None) -> dict[str, Any] | None:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        return self.repository.get_manifest(owner_id, import_id)

    def save_state(
        self,
        owner_id: str | ImportJob,
        job: ImportJob | Mapping[str, Any],
        manifest: Mapping[str, Any] | None = None,
    ) -> None:
        if manifest is None:
            manifest = job
            job = owner_id
            owner_id = None
        self.repository.save_state(owner_id, job, manifest)


def _metadata_text(value: object, field_name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ImportValidationError(f"invalid_{field_name}", f"{field_name} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum or any(ord(character) < 32 for character in text):
        raise ImportValidationError(f"invalid_{field_name}", f"{field_name} is not valid metadata")
    return text


def _file_id(value: object) -> str:
    if value is None:
        return str(uuid4())
    if not isinstance(value, str) or not _FILE_ID.fullmatch(value.strip()):
        raise ImportValidationError("invalid_file_id", "file_id is not valid")
    return value.strip()


def _total_bytes(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ImportValidationError("invalid_total_bytes", "total_bytes must be a non-negative integer")
    return value


def _sha256(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ImportValidationError("invalid_file_sha256", "sha256 must be a 64-character hexadecimal digest")
    return value.lower()


def _build_files(
    files: Sequence[Mapping[str, Any]] | None,
    source_name: object,
    media_type: object,
    total_bytes: object,
) -> tuple[ImportFile, ...]:
    if files is None:
        if source_name is None or media_type is None or total_bytes is None:
            raise TypeError("source_name, total_bytes, and media_type are required")
        return (ImportFile.create(source_name, media_type, total_bytes),)
    if isinstance(files, (str, bytes, bytearray)) or not isinstance(files, Sequence) or not files:
        raise ImportValidationError("invalid_manifest", "files must be a non-empty list")
    if len(files) > MAX_IMPORT_FILES:
        raise ImportValidationError("manifest_too_many_files", "manifest contains too many files")

    parsed_items: list[ImportFile] = []
    for item in files:
        if isinstance(item, ImportFile):
            parsed_items.append(item)
            continue
        if not isinstance(item, Mapping):
            _invalid_file_object()
        parsed_items.append(
            ImportFile.create(
                source_name=item.get("source_name"),
                media_type=item.get("media_type"),
                total_bytes=item.get("total_bytes"),
                sha256=item.get("sha256"),
                file_id=item.get("file_id"),
            )
        )
    parsed = tuple(parsed_items)
    ids = [item.file_id for item in parsed]
    if len(ids) != len(set(ids)):
        raise ImportValidationError("duplicate_file_id", "manifest contains duplicate file_id values")
    if total_bytes is not None and _total_bytes(total_bytes) != sum(item.total_bytes for item in parsed):
        raise ImportValidationError("manifest_total_mismatch", "total_bytes must equal the file size sum")
    return parsed


def _invalid_file_object() -> ImportFile:
    raise ImportValidationError("invalid_file", "each file must be an object")


def _stored_files(value: object) -> tuple[ImportFile, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or not value:
        raise ImportValidationError("invalid_manifest", "files must be a non-empty list")
    parsed = tuple(ImportFile.from_mapping(item) for item in value)
    if len({item.file_id for item in parsed}) != len(parsed):
        raise ImportValidationError("duplicate_file_id", "manifest contains duplicate file_id values")
    return parsed
