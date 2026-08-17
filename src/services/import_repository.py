"""Transactional encrypted persistence for import jobs and upload manifests."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import MetadataStore, require_metadata_store


class ImportRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ImportRepository:
    """Stores the job and its resumable manifest in one encrypted transaction."""

    _RECORD_VERSION = 1
    _JOB_AAD_PREFIX = "past-partner/import-job/v1/"
    _MANIFEST_AAD_PREFIX = "past-partner/import-manifest/v1/"

    def __init__(
        self,
        database_path: Path | str | MetadataStore,
        encryption: AuthenticatedEncryptionService,
    ) -> None:
        self.metadata_store = require_metadata_store(database_path)
        self.database_path = getattr(self.metadata_store, "database_path", None)
        self.encryption = encryption
        self.metadata_store.migrate()

    def create(
        self,
        owner_id: str | Any,
        job: Any | Mapping[str, Any] | None = None,
        manifest: Mapping[str, Any] | None = None,
    ) -> None:
        if owner_id is not None and not isinstance(owner_id, str):
            manifest = job if isinstance(job, Mapping) else manifest
            job = owner_id
            owner_id = None
        owner_id = self._owner_id(owner_id)
        payload = self._encode_job(job)
        manifest_value = self._normalize_manifest(job.id, manifest, getattr(job, "files", ()))
        manifest_payload = self._encode_manifest(job.id, manifest_value)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO imports (id, owner_id, record_version, encrypted_payload)
                VALUES (?, ?, ?, ?)
                """,
                (job.id, owner_id, self._RECORD_VERSION, payload),
            )
            connection.execute(
                """
                INSERT INTO import_manifests (import_id, record_version, encrypted_payload)
                VALUES (?, ?, ?)
                """,
                (job.id, self._RECORD_VERSION, manifest_payload),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise ImportRepositoryError("import_exists", "import already exists") from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, owner_id: str, import_id: str | None = None) -> Any | None:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        owner_id = self._owner_id(owner_id)
        if not isinstance(import_id, str) or not import_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT record_version, encrypted_payload FROM imports WHERE id = ? AND {self._owner_clause(owner_id)}",
                (import_id, *self._owner_params(owner_id)),
            ).fetchone()
        if row is None:
            return None
        return self._decode_job(import_id, row[0], row[1])

    def get_manifest(self, owner_id: str, import_id: str | None = None) -> dict[str, Any] | None:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        owner_id = self._owner_id(owner_id)
        if not isinstance(import_id, str) or not import_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""
                SELECT m.record_version, m.encrypted_payload
                FROM import_manifests AS m
                JOIN imports AS i ON i.id = m.import_id
                WHERE m.import_id = ? AND {self._owner_clause(owner_id, table_alias='i')}
                """,
                (import_id, *self._owner_params(owner_id)),
            ).fetchone()
        if row is None:
            return None
        return self._decode_manifest(import_id, row[0], row[1])

    def list(self, owner_id: str | None = None) -> list[Any]:
        owner_id = self._owner_id(owner_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT rowid, id, record_version, encrypted_payload FROM imports WHERE {self._owner_clause(owner_id)}",
                self._owner_params(owner_id),
            ).fetchall()
        jobs = [(row[0], self._decode_job(row[1], row[2], row[3])) for row in rows]
        return [
            job
            for _, job in sorted(jobs, key=lambda item: (item[1].created_at, item[0]))
        ]

    def list_for_persona(self, owner_id: str, persona_id: str) -> list[Any]:
        owner_id = self._owner_id(owner_id)
        if not isinstance(persona_id, str) or not persona_id:
            return []
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT rowid, id, record_version, encrypted_payload FROM imports WHERE {self._owner_clause(owner_id)}",
                self._owner_params(owner_id),
            ).fetchall()
        jobs = [
            (row[0], job)
            for row in rows
            for job in [self._decode_job(row[1], row[2], row[3])]
            if job.persona_id == persona_id
        ]
        return [
            job
            for _, job in sorted(jobs, key=lambda item: (item[1].created_at, item[0]))
        ]

    def list_expired_terminal(self, owner_id: str, cutoff: datetime) -> list[Any]:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must be timezone-aware")
        cutoff_utc = cutoff.astimezone(UTC)
        expired = []
        for job in self.list(owner_id):
            if getattr(job.state, "value", None) not in {"failed", "cancelled"}:
                continue
            try:
                updated_at = datetime.fromisoformat(job.updated_at.replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError):
                continue
            if updated_at.tzinfo is None or updated_at.utcoffset() is None:
                continue
            if updated_at.astimezone(UTC) < cutoff_utc:
                expired.append(job)
        return expired

    def delete(self, owner_id: str, import_id: str | None = None) -> bool:
        if import_id is None:
            import_id = owner_id
            owner_id = None
        owner_id = self._owner_id(owner_id)
        if not isinstance(import_id, str) or not import_id:
            return False
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                f"SELECT 1 FROM imports WHERE id = ? AND {self._owner_clause(owner_id)}",
                (import_id, *self._owner_params(owner_id)),
            ).fetchone()
            if existing is None:
                connection.rollback()
                return False
            connection.execute("DELETE FROM import_manifests WHERE import_id = ?", (import_id,))
            deleted = connection.execute(
                f"DELETE FROM imports WHERE id = ? AND {self._owner_clause(owner_id)}",
                (import_id, *self._owner_params(owner_id)),
            ).rowcount
            connection.commit()
            return deleted == 1
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def save(self, owner_id: str | Any, job: Any | None = None) -> None:
        if job is None:
            job = owner_id
            owner_id = None
        owner_id = self._owner_id(owner_id)
        job_payload = self._encode_job(job)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                f"""
                UPDATE imports
                SET record_version = ?, encrypted_payload = ?
                WHERE id = ? AND {self._owner_clause(owner_id)}
                """,
                (self._RECORD_VERSION, job_payload, job.id, *self._owner_params(owner_id)),
            ).rowcount
            if updated != 1:
                raise ImportRepositoryError("import_not_found", "import not found")
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def save_state(
        self,
        owner_id: str | Any,
        job: Any | Mapping[str, Any],
        manifest: Mapping[str, Any] | None = None,
    ) -> None:
        if manifest is None:
            manifest = job
            job = owner_id
            owner_id = None
        owner_id = self._owner_id(owner_id)
        job_payload = self._encode_job(job)
        manifest_value = self._normalize_manifest(job.id, manifest, getattr(job, "files", ()))
        manifest_payload = self._encode_manifest(job.id, manifest_value)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                f"SELECT 1 FROM imports WHERE id = ? AND {self._owner_clause(owner_id)}",
                (job.id, *self._owner_params(owner_id)),
            ).fetchone()
            if existing is None:
                raise ImportRepositoryError("import_not_found", "import not found")
            connection.execute(
                f"""
                UPDATE imports
                SET record_version = ?, encrypted_payload = ?
                WHERE id = ? AND {self._owner_clause(owner_id)}
                """,
                (self._RECORD_VERSION, job_payload, job.id, *self._owner_params(owner_id)),
            )
            connection.execute(
                """
                INSERT INTO import_manifests (import_id, record_version, encrypted_payload)
                VALUES (?, ?, ?)
                ON CONFLICT(import_id) DO UPDATE SET
                    record_version = excluded.record_version,
                    encrypted_payload = excluded.encrypted_payload
                """,
                (job.id, self._RECORD_VERSION, manifest_payload),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def assign_unowned(self, owner_id: str) -> int:
        owner_id = self._owner_id(owner_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE imports SET owner_id = ? WHERE owner_id IS NULL",
                (owner_id,),
            ).rowcount
            connection.commit()
            return updated
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def migrate_legacy_json(
        self,
        imports_directory: Path | str,
        manifests_directory: Path | str,
        owner_id: str | None = None,
    ) -> int:
        """Encrypt legacy metadata, then remove plaintext sources after commit."""

        owner_id = self._owner_id(owner_id)

        import_dir = Path(imports_directory).expanduser().resolve()
        manifest_dir = Path(manifests_directory).expanduser().resolve()
        import_records = self._read_legacy_jobs(import_dir)
        manifest_records = self._read_legacy_manifests(manifest_dir)
        if not import_records and not manifest_records:
            return 0

        for import_id in manifest_records:
            if import_id not in import_records:
                raise ImportRepositoryError(
                    "legacy_manifest_orphan", "legacy manifest has no import job"
                )

        records: list[tuple[str, Any, dict[str, Any]]] = []
        for import_id, job in import_records.items():
            manifest = manifest_records.get(
                import_id,
                self._default_manifest(import_id, getattr(job, "files", ())),
            )
            records.append((import_id, job, manifest))

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for import_id, job, manifest in records:
                existing_job = connection.execute(
                    """
                    SELECT owner_id, record_version, encrypted_payload
                    FROM imports WHERE id = ?
                    """,
                    (import_id,),
                ).fetchone()
                existing_manifest = connection.execute(
                    """
                    SELECT record_version, encrypted_payload
                    FROM import_manifests
                    WHERE import_id = ?
                    """,
                    (import_id,),
                ).fetchone()
                if existing_job is None:
                    connection.execute(
                        """
                        INSERT INTO imports (id, owner_id, record_version, encrypted_payload)
                        VALUES (?, ?, ?, ?)
                        """,
                        (import_id, owner_id, self._RECORD_VERSION, self._encode_job(job)),
                    )
                    connection.execute(
                        """
                        INSERT INTO import_manifests (import_id, record_version, encrypted_payload)
                        VALUES (?, ?, ?)
                        """,
                        (import_id, self._RECORD_VERSION, self._encode_manifest(import_id, manifest)),
                    )
                else:
                    if (
                        existing_job[0] != owner_id
                        or self._decode_job(import_id, existing_job[1], existing_job[2]) != job
                    ):
                        raise ImportRepositoryError(
                            "legacy_import_conflict", "legacy import conflicts with encrypted record"
                        )
                    if existing_manifest is None:
                        connection.execute(
                            """
                            INSERT INTO import_manifests (import_id, record_version, encrypted_payload)
                            VALUES (?, ?, ?)
                            """,
                            (import_id, self._RECORD_VERSION, self._encode_manifest(import_id, manifest)),
                        )
                    elif self._decode_manifest(
                        import_id, existing_manifest[0], existing_manifest[1]
                    ) != manifest:
                        raise ImportRepositoryError(
                            "legacy_manifest_conflict",
                            "legacy manifest conflicts with encrypted record",
                        )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

        for path in [*self._paths(import_dir), *self._paths(manifest_dir)]:
            try:
                path.unlink()
            except OSError as exc:
                raise ImportRepositoryError(
                    "legacy_import_cleanup_failed", "legacy import source could not be removed"
                ) from exc
        return len(records)

    def _read_legacy_jobs(self, directory: Path) -> dict[str, Any]:
        records: dict[str, Any] = {}
        for path in self._paths(directory):
            try:
                from src.services.import_service import ImportJob

                job = ImportJob.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ImportRepositoryError(
                    "legacy_import_record_invalid", "legacy import record is invalid"
                ) from exc
            if job.id != path.stem:
                raise ImportRepositoryError(
                    "legacy_import_identity_mismatch", "legacy import filename does not match its record"
                )
            records[job.id] = job
        return records

    def _read_legacy_manifests(self, directory: Path) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        for path in self._paths(directory):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ImportRepositoryError(
                    "legacy_manifest_record_invalid", "legacy manifest record is invalid"
                ) from exc
            try:
                manifest = self._normalize_manifest(path.stem, value)
            except ImportRepositoryError as exc:
                raise ImportRepositoryError(
                    "legacy_manifest_record_invalid", "legacy manifest record is invalid"
                ) from exc
            records[path.stem] = manifest
        return records

    @staticmethod
    def _paths(directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        if not directory.is_dir():
            raise ImportRepositoryError("legacy_import_directory_invalid", "legacy import path is not a directory")
        paths = sorted(directory.glob("*.json"))
        for path in paths:
            if path.is_symlink() or path.resolve().parent != directory:
                raise ImportRepositoryError("legacy_import_path_invalid", "legacy import path is unsafe")
        return paths

    def _decode_job(self, import_id: str, record_version: object, envelope: object) -> Any:
        if record_version != self._RECORD_VERSION or not isinstance(envelope, bytes):
            raise ImportRepositoryError("import_record_version_unsupported", "import record version is unsupported")
        try:
            payload = self.encryption.decrypt(envelope, self._job_aad(import_id))
        except (AuthenticationError, InvalidEncryptedPayloadError) as exc:
            raise ImportRepositoryError(
                "import_record_authentication_failed", "import record authentication failed"
            ) from exc
        try:
            from src.services.import_service import ImportJob

            job = ImportJob.from_dict(json.loads(payload.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ImportRepositoryError("import_record_corrupt", "import record is invalid") from exc
        if job.id != import_id:
            raise ImportRepositoryError("import_record_corrupt", "import record identity mismatches")
        return job

    def _decode_manifest(self, import_id: str, record_version: object, envelope: object) -> dict[str, Any]:
        if record_version != self._RECORD_VERSION or not isinstance(envelope, bytes):
            raise ImportRepositoryError(
                "manifest_record_version_unsupported", "manifest record version is unsupported"
            )
        try:
            payload = self.encryption.decrypt(envelope, self._manifest_aad(import_id))
        except (AuthenticationError, InvalidEncryptedPayloadError) as exc:
            raise ImportRepositoryError(
                "manifest_record_authentication_failed", "manifest record authentication failed"
            ) from exc
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImportRepositoryError("manifest_record_corrupt", "manifest record is invalid") from exc
        return self._normalize_manifest(import_id, value)

    def _encode_job(self, job: Any) -> bytes:
        if not hasattr(job, "id") or not hasattr(job, "to_dict"):
            raise TypeError("job must be an ImportJob")
        return self._encrypt_json(job.to_dict(), self._job_aad(job.id))

    def _encode_manifest(self, import_id: str, manifest: Mapping[str, Any]) -> bytes:
        return self._encrypt_json(dict(manifest), self._manifest_aad(import_id))

    def _encrypt_json(self, value: Mapping[str, Any], aad: bytes) -> bytes:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self.encryption.encrypt(payload, aad)

    @classmethod
    def _job_aad(cls, import_id: str) -> bytes:
        return f"{cls._JOB_AAD_PREFIX}{import_id}".encode("utf-8")

    @classmethod
    def _manifest_aad(cls, import_id: str) -> bytes:
        return f"{cls._MANIFEST_AAD_PREFIX}{import_id}".encode("utf-8")

    @staticmethod
    def _default_manifest(import_id: str, files: Sequence[Any] = ()) -> dict[str, Any]:
        manifest: dict[str, Any] = {"version": 2, "import_id": import_id, "chunks": {}}
        if files:
            manifest["files"] = [
                {
                    **item.to_dict(),
                    "received_bytes": 0,
                    "chunk_count": 0,
                    "chunks": {},
                }
                for item in files
            ]
        return manifest

    def _normalize_manifest(
        self,
        import_id: str,
        value: Mapping[str, Any] | None,
        files: Sequence[Any] = (),
    ) -> dict[str, Any]:
        if value is None:
            manifest = self._default_manifest(import_id, files)
        elif isinstance(value, Mapping):
            manifest = dict(value)
        else:
            raise ImportRepositoryError("manifest_record_corrupt", "manifest record is invalid")
        if (
            manifest.get("version") != 2
            or manifest.get("import_id") != import_id
            or not isinstance(manifest.get("chunks"), dict)
        ):
            raise ImportRepositoryError("manifest_record_corrupt", "manifest record is invalid")

        raw_files = manifest.get("files")
        if raw_files is None:
            if files:
                manifest["files"] = self._default_manifest(import_id, files)["files"]
            return manifest

        normalized_files = self._normalize_file_manifest(raw_files)
        if files:
            expected = [item.to_dict() for item in files]
            actual = [
                {key: item[key] for key in expected[0]}
                for item in normalized_files
            ] if expected else []
            if actual != expected:
                raise ImportRepositoryError(
                    "manifest_file_mismatch",
                    "manifest files do not match the import job",
                )
        manifest["files"] = normalized_files
        return manifest

    @staticmethod
    def _normalize_file_manifest(value: object) -> list[dict[str, Any]]:
        from src.services.import_service import ImportFile, ImportValidationError

        if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence) or not value:
            raise ImportRepositoryError("manifest_record_corrupt", "manifest files are invalid")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in value:
            try:
                parsed = ImportFile.from_mapping(item)
            except (ImportValidationError, TypeError) as exc:
                raise ImportRepositoryError("manifest_record_corrupt", "manifest files are invalid") from exc
            if parsed.file_id in seen:
                raise ImportRepositoryError("manifest_record_corrupt", "manifest contains duplicate file IDs")
            seen.add(parsed.file_id)
            entry = dict(item)
            entry.update(parsed.to_dict())
            chunks = entry.get("chunks", {})
            if not isinstance(chunks, dict):
                raise ImportRepositoryError("manifest_record_corrupt", "file chunks are invalid")
            entry["received_bytes"] = entry.get("received_bytes", 0)
            entry["chunk_count"] = entry.get("chunk_count", len(chunks))
            entry["chunks"] = chunks
            normalized.append(entry)
        return normalized

    def _connect(self) -> sqlite3.Connection:
        return self.metadata_store.connect()

    @staticmethod
    def _owner_id(owner_id: object) -> str | None:
        if owner_id is None:
            return None
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("owner_id must be a non-empty string")
        return owner_id.strip()

    @staticmethod
    def _owner_clause(owner_id: str | None, table_alias: str | None = None) -> str:
        column = f"{table_alias}.owner_id" if table_alias else "owner_id"
        return f"{column} IS NULL" if owner_id is None else f"{column} = ?"

    @staticmethod
    def _owner_params(owner_id: str | None) -> tuple[str, ...]:
        return () if owner_id is None else (owner_id,)
