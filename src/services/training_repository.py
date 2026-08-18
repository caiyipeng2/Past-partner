"""Authenticated encrypted persistence for owner-scoped fine-tuning jobs."""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path

from src.domain.training_jobs import TrainingJob, TrainingJobValidationError
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import MetadataConnection, MetadataIntegrityError, MetadataStore, require_metadata_store


class TrainingJobRepositoryError(RuntimeError):
    """A stable persistence boundary for encrypted training metadata."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class TrainingJobRepository:
    """Persist only redacted job metadata; never dataset paths or source messages."""

    _RECORD_VERSION = 1
    _AAD_PREFIX = "past-partner/training-job/v1/"

    def __init__(self, database_path: Path | str | MetadataStore, encryption: AuthenticatedEncryptionService) -> None:
        self.metadata_store = require_metadata_store(database_path)
        self.database_path = getattr(self.metadata_store, "database_path", None)
        self.encryption = encryption
        self.metadata_store.migrate()

    def save(self, owner_id: str, job: TrainingJob) -> TrainingJob:
        """Write one state transition only when its encrypted revision is current.

        The revision is intentionally checked in the same immediate SQLite
        transaction as the encrypted envelope update. A stale HTTP poll, cancel,
        or persona cleanup can therefore fail closed instead of reviving a newer
        terminal state or a previously deleted job.
        """

        owner = self._owner_id(owner_id)
        if not isinstance(job, TrainingJob):
            raise TypeError("job must be a TrainingJob")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_id, revision FROM training_jobs WHERE id = ?", (job.id,)
            ).fetchone()
            if row is None:
                if job.revision != 0:
                    raise TrainingJobRepositoryError(
                        "training_job_conflict",
                        "training job was removed or changed by another request",
                    )
                stored = replace(job, revision=1)
                connection.execute(
                    """
                    INSERT INTO training_jobs
                        (id, owner_id, persona_id, record_version, revision, encrypted_payload)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stored.id,
                        owner,
                        stored.persona_id,
                        self._RECORD_VERSION,
                        stored.revision,
                        self._encode(owner, stored),
                    ),
                )
            elif row[0] != owner:
                raise TrainingJobRepositoryError(
                    "training_job_exists", "training job ID belongs to another owner"
                )
            else:
                current_revision = row[1]
                if not isinstance(current_revision, int) or job.revision != current_revision:
                    raise TrainingJobRepositoryError(
                        "training_job_conflict",
                        "training job was changed by another request",
                    )
                stored = replace(job, revision=current_revision + 1)
                updated = connection.execute(
                    """
                    UPDATE training_jobs
                    SET persona_id = ?, record_version = ?, revision = ?, encrypted_payload = ?
                    WHERE id = ? AND owner_id = ? AND revision = ?
                    """,
                    (
                        stored.persona_id,
                        self._RECORD_VERSION,
                        stored.revision,
                        self._encode(owner, stored),
                        stored.id,
                        owner,
                        job.revision,
                    ),
                ).rowcount
                if updated != 1:
                    raise TrainingJobRepositoryError(
                        "training_job_conflict",
                        "training job was changed or removed by another request",
                    )
            connection.commit()
            return stored
        except MetadataIntegrityError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise TrainingJobRepositoryError(
                "training_job_references_invalid", "training job references an unavailable owner or persona"
            ) from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, owner_id: str, job_id: str) -> TrainingJob | None:
        owner = self._owner_id(owner_id)
        if not isinstance(job_id, str) or not job_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT persona_id, record_version, revision, encrypted_payload
                FROM training_jobs WHERE id = ? AND owner_id = ?
                """,
                (job_id, owner),
            ).fetchone()
        if row is None:
            return None
        return self._decode(owner, job_id, row[0], row[1], row[2], row[3])

    def list(self, owner_id: str, persona_id: str | None = None) -> list[TrainingJob]:
        owner = self._owner_id(owner_id)
        query = (
            "SELECT id, persona_id, record_version, revision, encrypted_payload "
            "FROM training_jobs WHERE owner_id = ?"
        )
        parameters: list[str] = [owner]
        if persona_id is not None:
            if not isinstance(persona_id, str) or not persona_id:
                return []
            query += " AND persona_id = ?"
            parameters.append(persona_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        jobs = [self._decode(owner, row[0], row[1], row[2], row[3], row[4]) for row in rows]
        return sorted(jobs, key=lambda job: (job.created_at, job.id))

    def delete(self, owner_id: str, job_id: str) -> bool:
        owner = self._owner_id(owner_id)
        if not isinstance(job_id, str) or not job_id:
            return False
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute(
                "DELETE FROM training_jobs WHERE id = ? AND owner_id = ?", (job_id, owner)
            ).rowcount
            connection.commit()
            return deleted == 1
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def delete_for_persona(self, owner_id: str, persona_id: str) -> int:
        owner = self._owner_id(owner_id)
        if not isinstance(persona_id, str) or not persona_id:
            return 0
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute(
                "DELETE FROM training_jobs WHERE owner_id = ? AND persona_id = ?",
                (owner, persona_id),
            ).rowcount
            connection.commit()
            return deleted
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _encode(self, owner_id: str, job: TrainingJob) -> bytes:
        return self.encryption.encrypt(
            json.dumps(job.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
                "utf-8"
            ),
            self._aad(owner_id, job.id),
        )

    def _decode(
        self,
        owner_id: str,
        job_id: str,
        persona_id: object,
        record_version: object,
        revision: object,
        envelope: object,
    ) -> TrainingJob:
        if (
            record_version != self._RECORD_VERSION
            or not isinstance(revision, int)
            or revision <= 0
            or not isinstance(envelope, bytes)
        ):
            raise TrainingJobRepositoryError(
                "training_job_record_version_unsupported",
                "training job record version is unsupported",
            )
        try:
            payload = self.encryption.decrypt(envelope, self._aad(owner_id, job_id))
        except (AuthenticationError, InvalidEncryptedPayloadError) as exc:
            raise TrainingJobRepositoryError(
                "training_job_record_authentication_failed",
                "training job record authentication failed",
            ) from exc
        try:
            value = json.loads(payload.decode("utf-8"))
            job = TrainingJob.from_dict(value)
        except (UnicodeDecodeError, json.JSONDecodeError, TrainingJobValidationError) as exc:
            raise TrainingJobRepositoryError(
                "training_job_record_corrupt", "training job record is invalid"
            ) from exc
        if job.id != job_id or job.persona_id != persona_id:
            raise TrainingJobRepositoryError(
                "training_job_record_corrupt", "training job record identity does not match its index"
            )
        if job.revision == 0:
            # Migration 7 adds the database revision after earlier encrypted job
            # envelopes may already exist. Reading them upgrades the in-memory
            # object; its next write re-encrypts the explicit revision.
            job = replace(job, revision=revision)
        elif job.revision != revision:
            raise TrainingJobRepositoryError(
                "training_job_record_corrupt",
                "training job revision does not match its encrypted metadata",
            )
        return job

    @classmethod
    def _aad(cls, owner_id: str, job_id: str) -> bytes:
        return f"{cls._AAD_PREFIX}{owner_id}/{job_id}".encode("utf-8")

    def _connect(self) -> MetadataConnection:
        return self.metadata_store.connect()

    @staticmethod
    def _owner_id(owner_id: object) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id must be a non-empty string")
        return owner_id.strip()
