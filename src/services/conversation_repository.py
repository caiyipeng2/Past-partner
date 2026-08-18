"""Encrypted SQLite persistence for owner/persona-scoped conversations."""

from __future__ import annotations

import json
from contextlib import closing
from pathlib import Path

from src.domain.conversations import Conversation, ConversationValidationError
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import MetadataConnection, MetadataIntegrityError, MetadataStore, require_metadata_store


class ConversationRepositoryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ConversationRepository:
    _RECORD_VERSION = 1
    _AAD_PREFIX = "past-partner/conversation/v1/"

    def __init__(self, database_path: Path | str | MetadataStore, encryption: AuthenticatedEncryptionService) -> None:
        self.metadata_store = require_metadata_store(database_path)
        self.database_path = getattr(self.metadata_store, "database_path", None)
        self.encryption = encryption
        self.metadata_store.migrate()

    def save(self, owner_id: str, conversation: Conversation) -> Conversation:
        owner = self._owner_id(owner_id)
        envelope = self._encode(owner, conversation)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO conversations
                    (id, owner_id, persona_id, record_version, encrypted_payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation.id, owner, conversation.persona_id, self._RECORD_VERSION, envelope),
            )
            connection.commit()
            return conversation
        except MetadataIntegrityError as exc:
            if connection.in_transaction:
                connection.rollback()
            code = "conversation_exists" if "UNIQUE" in str(exc).upper() else "conversation_references_invalid"
            message = "conversation already exists" if code == "conversation_exists" else "conversation references are invalid"
            raise ConversationRepositoryError(code, message) from exc
        finally:
            connection.close()

    def replace(self, owner_id: str, conversation: Conversation) -> Conversation | None:
        owner = self._owner_id(owner_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT persona_id, record_version FROM conversations WHERE id = ? AND owner_id = ?",
                (conversation.id, owner),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            if row[0] != conversation.persona_id or row[1] != self._RECORD_VERSION:
                raise ConversationRepositoryError("conversation_record_invalid", "conversation record is invalid")
            connection.execute(
                "UPDATE conversations SET encrypted_payload = ? WHERE id = ? AND owner_id = ?",
                (self._encode(owner, conversation), conversation.id, owner),
            )
            connection.commit()
            return conversation
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def append_messages(self, owner_id: str, conversation: Conversation) -> Conversation | None:
        return self.replace(owner_id, conversation)

    def get(self, owner_id: str, conversation_id: str) -> Conversation | None:
        owner = self._owner_id(owner_id)
        if not isinstance(conversation_id, str) or not conversation_id:
            return None
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT persona_id, record_version, encrypted_payload
                FROM conversations WHERE id = ? AND owner_id = ?
                """,
                (conversation_id, owner),
            ).fetchone()
        if row is None:
            return None
        return self._decode(owner, conversation_id, row[0], row[1], row[2])

    def list(self, owner_id: str, persona_id: str | None = None) -> list[Conversation]:
        owner = self._owner_id(owner_id)
        query = (
            "SELECT id, persona_id, record_version, encrypted_payload "
            "FROM conversations WHERE owner_id = ?"
        )
        parameters: list[str] = [owner]
        if persona_id is not None:
            if not isinstance(persona_id, str) or not persona_id:
                return []
            query += " AND persona_id = ?"
            parameters.append(persona_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(query, parameters).fetchall()
        records = [self._decode(owner, row[0], row[1], row[2], row[3]) for row in rows]
        return sorted(records, key=lambda item: (item.updated_at, item.id), reverse=True)

    def delete_for_persona(self, owner_id: str, persona_id: str) -> int:
        owner = self._owner_id(owner_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            deleted = connection.execute(
                "DELETE FROM conversations WHERE owner_id = ? AND persona_id = ?",
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

    def _encode(self, owner_id: str, conversation: Conversation) -> bytes:
        return self.encryption.encrypt(
            json.dumps(conversation.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            self._aad(owner_id, conversation.id),
        )

    def _decode(
        self,
        owner_id: str,
        conversation_id: str,
        persona_id: object,
        record_version: object,
        envelope: object,
    ) -> Conversation:
        if record_version != self._RECORD_VERSION or not isinstance(envelope, bytes):
            raise ConversationRepositoryError("conversation_record_version_unsupported", "conversation record is unsupported")
        try:
            payload = self.encryption.decrypt(envelope, self._aad(owner_id, conversation_id))
            conversation = Conversation.from_dict(json.loads(payload.decode("utf-8")))
        except (AuthenticationError, InvalidEncryptedPayloadError) as exc:
            raise ConversationRepositoryError("conversation_record_authentication_failed", "conversation record authentication failed") from exc
        except (UnicodeDecodeError, json.JSONDecodeError, ConversationValidationError) as exc:
            raise ConversationRepositoryError("conversation_record_corrupt", "conversation record is invalid") from exc
        if conversation.id != conversation_id or conversation.persona_id != persona_id:
            raise ConversationRepositoryError("conversation_identity_mismatch", "conversation identity does not match its index")
        return conversation

    def _aad(self, owner_id: str, conversation_id: str) -> bytes:
        return f"{self._AAD_PREFIX}{owner_id}/{conversation_id}".encode("utf-8")

    @staticmethod
    def _owner_id(value: object) -> str:
        if not isinstance(value, str) or not value:
            raise ValueError("owner_id must be non-empty")
        return value

    def _connect(self) -> MetadataConnection:
        return self.metadata_store.connect()
