"""Bounded cleanup for imports that have reached an explicit terminal state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


class RetentionService:
    MAX_RETENTION_SECONDS = 5 * 365 * 24 * 60 * 60

    def __init__(
        self,
        imports: Any,
        uploads: Any,
        retention_seconds: int = 0,
        normalized_retention_seconds: int = 0,
    ) -> None:
        for name, value in (
            ("retention_seconds", retention_seconds),
            ("normalized_retention_seconds", normalized_retention_seconds),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an integer")
            if not 0 <= value <= self.MAX_RETENTION_SECONDS:
                raise ValueError(f"{name} must be between 0 and five years")
        self.imports = imports
        self.uploads = uploads
        self.retention_seconds = retention_seconds
        self.normalized_retention_seconds = normalized_retention_seconds

    def cleanup(self, owner_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        if self.retention_seconds == 0 and self.normalized_retention_seconds == 0:
            return {
                "enabled": False,
                "retention_seconds": 0,
                "deleted_count": 0,
                "deleted_import_ids": [],
                "normalized_enabled": self.normalized_retention_seconds > 0,
                "normalized_deleted_count": 0,
                "normalized_deleted_import_ids": [],
            }

        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        current = current.astimezone(UTC)
        cutoff = current - timedelta(seconds=self.retention_seconds)
        deleted_import_ids: list[str] = []
        if self.retention_seconds > 0:
            for job in self.imports.list_expired_terminal(owner_id, cutoff):
                self.uploads.delete_import(owner_id, job.id)
                deleted_import_ids.append(job.id)
        normalized_deleted_import_ids: list[str] = []
        normalized_cutoff = current - timedelta(seconds=self.normalized_retention_seconds)
        if self.normalized_retention_seconds > 0:
            raw_ids = set(deleted_import_ids)
            for job in self.imports.list_expired_normalized(owner_id, normalized_cutoff):
                if job.id in raw_ids:
                    continue
                self.uploads.delete_import(owner_id, job.id)
                normalized_deleted_import_ids.append(job.id)
        return {
            "enabled": True,
            "retention_seconds": self.retention_seconds,
            "cutoff": cutoff.isoformat(),
            "deleted_count": len(deleted_import_ids),
            "deleted_import_ids": deleted_import_ids,
            "normalized_enabled": self.normalized_retention_seconds > 0,
            "normalized_retention_seconds": self.normalized_retention_seconds,
            "normalized_cutoff": normalized_cutoff.isoformat(),
            "normalized_deleted_count": len(normalized_deleted_import_ids),
            "normalized_deleted_import_ids": normalized_deleted_import_ids,
        }
