"""Encrypted SQLite persistence for persona metadata."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.domain.personas import Persona, PersonaValidationError
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.database import SQLiteMigrator


class PersonaRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PersonaRepository:
    _RECORD_VERSION = 1
    _AAD_PREFIX = "past-partner/persona/v1/"

    def __init__(
        self,
        database_path: Path | str,
        encryption: AuthenticatedEncryptionService,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.encryption = encryption
        SQLiteMigrator(self.database_path).migrate()

    def save(self, persona: Persona) -> None:
        if not isinstance(persona, Persona):
            raise TypeError("persona must be a Persona")
        envelope = self._encode(persona)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO personas (id, record_version, encrypted_payload)
                VALUES (?, ?, ?)
                """,
                (persona.id, self._RECORD_VERSION, envelope),
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

    def get(self, persona_id: str) -> Persona | None:
        if not isinstance(persona_id, str) or not persona_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT record_version, encrypted_payload FROM personas WHERE id = ?",
                (persona_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode(persona_id, row[0], row[1])

    def list(self) -> list[Persona]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, record_version, encrypted_payload FROM personas"
            ).fetchall()
        personas = [self._decode(row[0], row[1], row[2]) for row in rows]
        return sorted(personas, key=lambda item: (item.created_at, item.id))

    def migrate_legacy_json(self, directory: Path | str) -> int:
        """Encrypt legacy persona JSON before removing each committed source file."""

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
                    "SELECT record_version, encrypted_payload FROM personas WHERE id = ?",
                    (persona.id,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        """
                        INSERT INTO personas (id, record_version, encrypted_payload)
                        VALUES (?, ?, ?)
                        """,
                        (persona.id, self._RECORD_VERSION, self._encode(persona)),
                    )
                elif self._decode(persona.id, row[0], row[1]) != persona:
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
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
