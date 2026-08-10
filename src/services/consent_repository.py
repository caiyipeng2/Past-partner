"""Encrypted SQLite persistence for owner-scoped media consent records."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from src.domain.consents import ConsentValidationError, MediaConsent
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.database import SQLiteMigrator


class ConsentRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ConsentRepository:
    _RECORD_VERSION = 1
    _AAD_PREFIX = "past-partner/consent/v1/"

    def __init__(self, database_path: Path | str, encryption: AuthenticatedEncryptionService) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.encryption = encryption
        SQLiteMigrator(self.database_path).migrate()

    def save(self, owner_id: str, consent: MediaConsent) -> None:
        payload = self._encode(consent)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO consents (id, owner_id, persona_id, record_version, encrypted_payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    persona_id = excluded.persona_id,
                    record_version = excluded.record_version,
                    encrypted_payload = excluded.encrypted_payload
                """,
                (consent.id, owner_id, consent.persona_id, self._RECORD_VERSION, payload),
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.rollback()
            raise ConsentRepositoryError("consent_exists", "consent already exists") from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get(self, owner_id: str, consent_id: str) -> MediaConsent | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT record_version, encrypted_payload FROM consents WHERE id = ? AND owner_id = ?",
                (consent_id, owner_id),
            ).fetchone()
        if row is None:
            return None
        return self._decode(consent_id, row[0], row[1])

    def list(self, owner_id: str, persona_id: str | None = None) -> list[MediaConsent]:
        query = "SELECT id, record_version, encrypted_payload FROM consents WHERE owner_id = ?"
        parameters: list[str] = [owner_id]
        if persona_id is not None:
            query += " AND persona_id = ?"
            parameters.append(persona_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        consents = [self._decode(row[0], row[1], row[2]) for row in rows]
        return sorted(consents, key=lambda item: (item.created_at, item.id))

    def delete_for_persona(self, owner_id: str, persona_id: str) -> int:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute(
                "DELETE FROM consents WHERE owner_id = ? AND persona_id = ?",
                (owner_id, persona_id),
            ).rowcount
            connection.commit()
            return deleted
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _encode(self, consent: MediaConsent) -> bytes:
        return self.encryption.encrypt(
            json.dumps(consent.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            self._aad(consent.id),
        )

    def _decode(self, consent_id: str, record_version: object, envelope: object) -> MediaConsent:
        if record_version != self._RECORD_VERSION or not isinstance(envelope, bytes):
            raise ConsentRepositoryError("consent_record_version_unsupported", "consent record version is unsupported")
        try:
            payload = self.encryption.decrypt(envelope, self._aad(consent_id))
            value = json.loads(payload.decode("utf-8"))
            consent = MediaConsent.from_dict(value)
        except (AuthenticationError, InvalidEncryptedPayloadError) as exc:
            raise ConsentRepositoryError("consent_record_authentication_failed", "consent record authentication failed") from exc
        except (UnicodeDecodeError, json.JSONDecodeError, ConsentValidationError) as exc:
            raise ConsentRepositoryError("consent_record_corrupt", "consent record is invalid") from exc
        if consent.id != consent_id:
            raise ConsentRepositoryError("consent_identity_mismatch", "consent record identity does not match its key")
        return consent

    def _aad(self, consent_id: str) -> bytes:
        return f"{self._AAD_PREFIX}{consent_id}".encode("utf-8")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
