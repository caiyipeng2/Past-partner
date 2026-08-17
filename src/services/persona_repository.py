"""Encrypted SQLite persistence for persona metadata."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Mapping

from src.domain.personas import Persona, PersonaValidationError
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import MetadataStore, require_metadata_store


class PersonaRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PersonaRepository:
    _RECORD_VERSION = 1
    _AAD_PREFIX = "past-partner/persona/v1/"

    def __init__(
        self,
        database_path: Path | str | MetadataStore,
        encryption: AuthenticatedEncryptionService,
    ) -> None:
        self.metadata_store = require_metadata_store(database_path)
        self.database_path = getattr(self.metadata_store, "database_path", None)
        self.encryption = encryption
        self.metadata_store.migrate()

    def save(self, owner_id: str | Persona, persona: Persona | None = None) -> None:
        if persona is None:
            persona = owner_id
            owner_id = None
        owner_id = self._owner_id(owner_id)
        if not isinstance(persona, Persona):
            raise TypeError("persona must be a Persona")
        envelope = self._encode(persona)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO personas (id, owner_id, record_version, encrypted_payload)
                VALUES (?, ?, ?, ?)
                """,
                (persona.id, owner_id, self._RECORD_VERSION, envelope),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise PersonaRepositoryError("persona_exists", "persona already exists") from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, owner_id: str, persona_id: str | None = None) -> Persona | None:
        if persona_id is None:
            persona_id = owner_id
            owner_id = None
        owner_id = self._owner_id(owner_id)
        if not isinstance(persona_id, str) or not persona_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT record_version, encrypted_payload FROM personas WHERE id = ? AND {self._owner_clause(owner_id)}",
                (persona_id, *self._owner_params(owner_id)),
            ).fetchone()
        if row is None:
            return None
        return self._decode(persona_id, row[0], row[1])

    def delete(self, owner_id: str, persona_id: str | None = None) -> bool:
        if persona_id is None:
            persona_id = owner_id
            owner_id = None
        owner_id = self._owner_id(owner_id)
        if not isinstance(persona_id, str) or not persona_id:
            return False
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute(
                f"DELETE FROM personas WHERE id = ? AND {self._owner_clause(owner_id)}",
                (persona_id, *self._owner_params(owner_id)),
            ).rowcount
            connection.commit()
            return deleted == 1
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def update(
        self,
        owner_id: str,
        persona_id: str,
        changes: Mapping[str, Any],
    ) -> Persona | None:
        owner_id = self._owner_id(owner_id)
        if not isinstance(persona_id, str) or not persona_id:
            return None

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT record_version, encrypted_payload FROM personas WHERE id = ? AND {self._owner_clause(owner_id)}",
                (persona_id, *self._owner_params(owner_id)),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None

            current = self._decode(persona_id, row[0], row[1])
            updated = current.update(changes)
            connection.execute(
                f"""
                UPDATE personas
                SET record_version = ?, encrypted_payload = ?
                WHERE id = ? AND {self._owner_clause(owner_id)}
                """,
                (
                    self._RECORD_VERSION,
                    self._encode(updated),
                    persona_id,
                    *self._owner_params(owner_id),
                ),
            )
            connection.commit()
            return updated
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def list(self, owner_id: str | None = None) -> list[Persona]:
        owner_id = self._owner_id(owner_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT id, record_version, encrypted_payload FROM personas WHERE {self._owner_clause(owner_id)}",
                self._owner_params(owner_id),
            ).fetchall()
        personas = [self._decode(row[0], row[1], row[2]) for row in rows]
        return sorted(personas, key=lambda item: (item.created_at, item.id))

    def assign_unowned(self, owner_id: str) -> int:
        owner_id = self._owner_id(owner_id)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                "UPDATE personas SET owner_id = ? WHERE owner_id IS NULL",
                (owner_id,),
            ).rowcount
            connection.commit()
        return updated

    def migrate_legacy_json(self, directory: Path | str, owner_id: str | None = None) -> int:
        """Encrypt legacy persona JSON before removing each committed source file."""

        owner_id = self._owner_id(owner_id)
        source_dir = Path(directory).expanduser().resolve()
        if not source_dir.exists():
            return 0
        if not source_dir.is_dir():
            raise PersonaRepositoryError("legacy_persona_directory_invalid", "legacy persona path is not a directory")

        records: list[tuple[Path, Persona]] = []
        for path in sorted(source_dir.glob("*.json")):
            if path.is_symlink() or path.resolve().parent != source_dir:
                raise PersonaRepositoryError("legacy_persona_path_invalid", "legacy persona path is unsafe")
            try:
                persona = Persona.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, PersonaValidationError) as exc:
                raise PersonaRepositoryError(
                    "legacy_persona_record_invalid", "legacy persona record is invalid"
                ) from exc
            if persona.id != path.stem:
                raise PersonaRepositoryError(
                    "legacy_persona_identity_mismatch", "legacy persona filename does not match its record"
                )
            records.append((path, persona))

        if not records:
            return 0

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for _, persona in records:
                row = connection.execute(
                    "SELECT owner_id, record_version, encrypted_payload FROM personas WHERE id = ?",
                    (persona.id,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO personas (id, owner_id, record_version, encrypted_payload)
                        VALUES (?, ?, ?, ?)
                        """,
                        (persona.id, owner_id, self._RECORD_VERSION, self._encode(persona)),
                    )
                elif row[0] != owner_id or self._decode(persona.id, row[1], row[2]) != persona:
                    raise PersonaRepositoryError(
                        "legacy_persona_conflict", "legacy persona conflicts with encrypted record"
                    )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

        for path, _ in records:
            try:
                path.unlink()
            except OSError as exc:
                raise PersonaRepositoryError(
                    "legacy_persona_cleanup_failed", "legacy persona source could not be removed"
                ) from exc
        return len(records)

    def _decode(self, persona_id: str, record_version: object, envelope: object) -> Persona:
        if record_version != self._RECORD_VERSION or not isinstance(envelope, bytes):
            raise PersonaRepositoryError(
                "persona_record_version_unsupported",
                "persona record version is unsupported",
            )
        try:
            payload = self.encryption.decrypt(envelope, self._aad(persona_id))
        except (AuthenticationError, InvalidEncryptedPayloadError) as exc:
            raise PersonaRepositoryError(
                "persona_record_authentication_failed",
                "persona record authentication failed",
            ) from exc
        try:
            value = json.loads(payload.decode("utf-8"))
            persona = Persona.from_dict(value)
        except (UnicodeDecodeError, json.JSONDecodeError, PersonaValidationError) as exc:
            raise PersonaRepositoryError(
                "persona_record_corrupt", "persona record is invalid"
            ) from exc
        if persona.id != persona_id:
            raise PersonaRepositoryError("persona_record_corrupt", "persona record identity mismatches")
        return persona

    def _encode(self, persona: Persona) -> bytes:
        payload = json.dumps(
            persona.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        return self.encryption.encrypt(payload, self._aad(persona.id))

    @classmethod
    def _aad(cls, persona_id: str) -> bytes:
        return f"{cls._AAD_PREFIX}{persona_id}".encode("utf-8")

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
    def _owner_clause(owner_id: str | None) -> str:
        return "owner_id IS NULL" if owner_id is None else "owner_id = ?"

    @staticmethod
    def _owner_params(owner_id: str | None) -> tuple[str, ...]:
        return () if owner_id is None else (owner_id,)
