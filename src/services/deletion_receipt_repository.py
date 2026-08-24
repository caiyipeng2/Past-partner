"""Anonymous deletion receipts that cannot be joined back to an owner."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
import json
from typing import Any, Mapping
from uuid import uuid4

from src.services.metadata_store import MetadataConnection, MetadataIntegrityError, MetadataStore, require_metadata_store


class DeletionReceiptRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class DeletionReceiptRepository:
    _RECORD_VERSION = 1

    def __init__(self, metadata_store: MetadataStore):
        self.metadata_store = require_metadata_store(metadata_store)
        self.metadata_store.migrate()

    def create(
        self,
        counts: Mapping[str, int],
        *,
        receipt_id: str | None = None,
        connection: MetadataConnection | None = None,
    ) -> dict[str, Any]:
        if not isinstance(counts, Mapping) or any(
            not isinstance(key, str)
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for key, value in counts.items()
        ):
            raise DeletionReceiptRepositoryError("receipt_counts_invalid", "deletion counts are invalid")
        normalized = {str(key): int(value) for key, value in counts.items()}
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(encoded) > 4096:
            raise DeletionReceiptRepositoryError("receipt_counts_invalid", "deletion counts are invalid")
        identifier = receipt_id or str(uuid4())
        deleted_at = datetime.now(UTC).isoformat()
        try:
            if connection is not None:
                connection.execute(
                    "INSERT INTO deletion_receipts (id, deleted_at, record_version, counts_json) VALUES (?, ?, ?, ?)",
                    (identifier, deleted_at, self._RECORD_VERSION, encoded),
                )
            else:
                with self.metadata_store.transaction(immediate=self.metadata_store.backend_name == "sqlite") as transaction:
                    transaction.execute(
                        "INSERT INTO deletion_receipts (id, deleted_at, record_version, counts_json) VALUES (?, ?, ?, ?)",
                        (identifier, deleted_at, self._RECORD_VERSION, encoded),
                    )
        except MetadataIntegrityError as exc:
            raise DeletionReceiptRepositoryError("receipt_exists", "deletion receipt already exists") from exc
        return {"receipt_id": identifier, "deleted_at": deleted_at, "counts": normalized}

    def get(self, receipt_id: str) -> dict[str, Any] | None:
        with closing(self.metadata_store.connect()) as connection:
            row = connection.execute(
                "SELECT id, deleted_at, record_version, counts_json FROM deletion_receipts WHERE id = ?",
                (receipt_id,),
            ).fetchone()
        if row is None:
            return None
        if row[2] != self._RECORD_VERSION or not isinstance(row[3], str):
            raise DeletionReceiptRepositoryError("receipt_corrupt", "deletion receipt is invalid")
        try:
            counts = json.loads(row[3])
        except json.JSONDecodeError as exc:
            raise DeletionReceiptRepositoryError("receipt_corrupt", "deletion receipt is invalid") from exc
        if not isinstance(counts, dict):
            raise DeletionReceiptRepositoryError("receipt_corrupt", "deletion receipt is invalid")
        return {"receipt_id": row[0], "deleted_at": row[1], "counts": counts}


__all__ = ["DeletionReceiptRepository", "DeletionReceiptRepositoryError"]
