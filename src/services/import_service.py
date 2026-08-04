"""Import job lifecycle and its persona ownership invariant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from src.services.import_repository import ImportRepository
from src.services.persona_service import PersonaNotFoundError, PersonaService


DEFAULT_MAX_IMPORT_BYTES = 3 * 1024**3


class ImportState(str, Enum):
    CREATED = "created"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ImportNotFoundError(LookupError):
    """Raised when an import job identifier is unknown."""


class ImportValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ImportJob":
        return cls(
            id=str(value["id"]),
            persona_id=str(value["persona_id"]),
            source_name=str(value["source_name"]),
            media_type=str(value["media_type"]),
            total_bytes=int(value["total_bytes"]),
            received_bytes=int(value.get("received_bytes", 0)),
            chunk_count=int(value.get("chunk_count", 0)),
            state=ImportState(value["state"]),
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
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
    ) -> ImportJob:
        if media_type is None:
            media_type = total_bytes
            total_bytes = source_name
            source_name = persona_id
            persona_id = owner_id
            owner_id = None
        if persona_id is None or source_name is None or total_bytes is None or media_type is None:
            raise TypeError("persona_id, source_name, total_bytes, and media_type are required")
        try:
            self.personas.get(owner_id, persona_id)
        except PersonaNotFoundError as exc:
            raise ImportValidationError("persona_not_found", "select an existing persona") from exc

        if isinstance(total_bytes, bool) or not isinstance(total_bytes, int) or total_bytes < 0:
            raise ImportValidationError("invalid_total_bytes", "total_bytes must be a non-negative integer")
        if total_bytes > self.max_import_bytes:
            raise ImportValidationError("import_too_large", "import exceeds the configured size limit")

        clean_source_name = _metadata_text(source_name, "source_name", maximum=512)
        clean_media_type = _metadata_text(media_type, "media_type", maximum=255)
        now = datetime.now(UTC).isoformat()
        job = ImportJob(
            id=str(uuid4()),
            persona_id=persona_id,
            source_name=clean_source_name,
            media_type=clean_media_type,
            total_bytes=total_bytes,
            received_bytes=0,
            chunk_count=0,
            state=ImportState.CREATED,
            created_at=now,
            updated_at=now,
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
