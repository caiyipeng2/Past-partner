"""Bounded cleanup for imports that have reached an explicit terminal state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


class RetentionService:
    MAX_RETENTION_SECONDS = 5 * 365 * 24 * 60 * 60

    def __init__(self, imports: Any, uploads: Any, retention_seconds: int = 0) -> None:
        if not isinstance(retention_seconds, int) or isinstance(retention_seconds, bool):
            raise ValueError("retention_seconds must be an integer")
        if not 0 <= retention_seconds <= self.MAX_RETENTION_SECONDS:
            raise ValueError("retention_seconds must be between 0 and five years")
        self.imports = imports
        self.uploads = uploads
        self.retention_seconds = retention_seconds

    def cleanup(self, owner_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        if self.retention_seconds == 0:
            return {
                "enabled": False,
                "retention_seconds": 0,
                "deleted_count": 0,
                "deleted_import_ids": [],
            }

        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        current = current.astimezone(UTC)
        cutoff = current - timedelta(seconds=self.retention_seconds)
        deleted_import_ids: list[str] = []
        for job in self.imports.list_expired_terminal(owner_id, cutoff):
            self.uploads.delete_import(owner_id, job.id)
            deleted_import_ids.append(job.id)
        return {
            "enabled": True,
            "retention_seconds": self.retention_seconds,
            "cutoff": cutoff.isoformat(),
            "deleted_count": len(deleted_import_ids),
            "deleted_import_ids": deleted_import_ids,
        }
