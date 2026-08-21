"""Encrypted owner/persona-scoped persistence for provider-independent learning state."""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, datetime
import json
import re
from typing import Any, Mapping
from uuid import uuid4

from src.learning.long_term_memory import LongTermMemory, MemoryCandidate
from src.learning.style_profile import StyleProfile
from src.learning.vector_retrieval import MemoryRetrievalResult, VectorMemoryRetriever, VectorRetrievalError
from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import MetadataIntegrityError, MetadataStore, require_metadata_store


class LearningRepositoryError(RuntimeError):
    """Stable persistence errors that do not expose driver or payload details."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


_RECORD_VERSION = 1
_INDEX_VERSION = 1
_ID = re.compile(r"^[a-fA-F0-9]{64}$")
_AAD_PROFILE = "past-partner/style-profile/v1/"
_AAD_MEMORY = "past-partner/long-term-memory/v1/"
_AAD_VECTOR = "past-partner/vector-index/v1/"


class LearningRepository:
    """Persist aggregate learning records and their encrypted retrieval index."""

    def __init__(self, metadata_store: MetadataStore, encryption: AuthenticatedEncryptionService) -> None:
        self.metadata_store = require_metadata_store(metadata_store)
        self.encryption = encryption
        self.metadata_store.migrate()

    def save_style_profile(self, owner_id: str, persona_id: str, profile: StyleProfile) -> StyleProfile:
        owner, persona = _scope(owner_id, persona_id)
        if not isinstance(profile, StyleProfile):
            raise TypeError("profile must be a StyleProfile")
        envelope = self._encode(profile.to_dict(), _AAD_PROFILE, owner, persona)
        now = datetime.now(UTC).isoformat()
        try:
            with self.metadata_store.transaction(immediate=self.metadata_store.backend_name == "sqlite") as connection:
                updated = connection.execute(
                    "UPDATE style_profiles SET record_version = ?, encrypted_payload = ?, updated_at = ? "
                    "WHERE owner_id = ? AND persona_id = ?",
                    (_RECORD_VERSION, envelope, now, owner, persona),
                ).rowcount
                if updated == 0:
                    connection.execute(
                        "INSERT INTO style_profiles "
                        "(id, owner_id, persona_id, record_version, encrypted_payload, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (str(uuid4()), owner, persona, _RECORD_VERSION, envelope, now),
                    )
        except MetadataIntegrityError as exc:
            raise LearningRepositoryError("learning_conflict", "learning record conflicts with existing state") from exc
        return profile

    def get_style_profile(self, owner_id: str, persona_id: str) -> StyleProfile | None:
        owner, persona = _scope(owner_id, persona_id)
        with closing(self.metadata_store.connect()) as connection:
            row = connection.execute(
                "SELECT record_version, encrypted_payload FROM style_profiles WHERE owner_id = ? AND persona_id = ?",
                (owner, persona),
            ).fetchone()
        if row is None:
            return None
        payload = self._decode(row[0], row[1], _AAD_PROFILE, owner, persona)
        return _profile_from_dict(payload)

    def save_memory(self, owner_id: str, persona_id: str, memory: LongTermMemory) -> LongTermMemory:
        owner, persona = _scope(owner_id, persona_id)
        if not isinstance(memory, LongTermMemory):
            raise TypeError("memory must be a LongTermMemory")
        memory_envelope = self._encode(memory.to_dict(), _AAD_MEMORY, owner, persona)
        index = VectorMemoryRetriever.build_index(memory)
        index_payload = {
            "index_version": _INDEX_VERSION,
            "entries": [
                {"memory_id": memory_id, "tokens": list(tokens)}
                for memory_id, tokens in sorted(index.items())
            ],
        }
        index_envelope = self._encode(index_payload, _AAD_VECTOR, owner, persona)
        now = datetime.now(UTC).isoformat()
        try:
            with self.metadata_store.transaction(immediate=self.metadata_store.backend_name == "sqlite") as connection:
                _upsert_aggregate(
                    connection,
                    "long_term_memories",
                    owner,
                    persona,
                    memory_envelope,
                    now,
                )
                _upsert_aggregate(
                    connection,
                    "vector_indexes",
                    owner,
                    persona,
                    index_envelope,
                    now,
                    extra_columns="index_version",
                    extra_values=(_INDEX_VERSION,),
                )
        except MetadataIntegrityError as exc:
            raise LearningRepositoryError("learning_conflict", "learning record conflicts with existing state") from exc
        return memory

    def get_memory(self, owner_id: str, persona_id: str) -> LongTermMemory | None:
        owner, persona = _scope(owner_id, persona_id)
        with closing(self.metadata_store.connect()) as connection:
            row = connection.execute(
                "SELECT record_version, encrypted_payload FROM long_term_memories WHERE owner_id = ? AND persona_id = ?",
                (owner, persona),
            ).fetchone()
        if row is None:
            return None
        return _memory_from_dict(self._decode(row[0], row[1], _AAD_MEMORY, owner, persona))

    def review_memory(
        self,
        owner_id: str,
        persona_id: str,
        memory_id: str,
        review_state: str,
    ) -> LongTermMemory:
        memory = self.get_memory(owner_id, persona_id)
        if memory is None:
            raise LearningRepositoryError("learning_not_found", "learning memory does not exist")
        try:
            updated = memory.review(memory_id, review_state)
        except ValueError as exc:
            code = getattr(exc, "code", "learning_review_invalid")
            raise LearningRepositoryError(code, "learning review state is invalid") from exc
        return self.save_memory(owner_id, persona_id, updated)

    def retrieve(
        self,
        owner_id: str,
        persona_id: str,
        query: str,
        *,
        as_of: str | None = None,
        max_candidates: int = 5,
        max_tokens: int = 800,
        max_age_days: int | None = None,
        allowed_speaker_scopes: tuple[str, ...] = ("persona", "user"),
    ) -> MemoryRetrievalResult:
        owner, persona = _scope(owner_id, persona_id)
        memory = self.get_memory(owner, persona)
        if memory is None:
            raise LearningRepositoryError("learning_not_found", "learning memory does not exist")
        with closing(self.metadata_store.connect()) as connection:
            row = connection.execute(
                "SELECT record_version, index_version, encrypted_payload FROM vector_indexes "
                "WHERE owner_id = ? AND persona_id = ?",
                (owner, persona),
            ).fetchone()
        if row is None:
            raise LearningRepositoryError("vector_index_missing", "learning vector index does not exist")
        payload = self._decode(row[0], row[2], _AAD_VECTOR, owner, persona)
        index = _vector_index_from_dict(payload, row[1], memory)
        try:
            return VectorMemoryRetriever().retrieve(
                memory,
                query,
                as_of=as_of,
                max_candidates=max_candidates,
                max_tokens=max_tokens,
                max_age_days=max_age_days,
                allowed_speaker_scopes=allowed_speaker_scopes,
                token_index=index,
            )
        except VectorRetrievalError:
            raise

    def delete_for_persona(self, owner_id: str, persona_id: str) -> dict[str, int]:
        owner, persona = _scope(owner_id, persona_id)
        counts: dict[str, int] = {}
        with self.metadata_store.transaction(immediate=self.metadata_store.backend_name == "sqlite") as connection:
            for table, key in (
                ("style_profiles", "style_profiles"),
                ("long_term_memories", "long_term_memories"),
                ("vector_indexes", "vector_indexes"),
            ):
                counts[key] = connection.execute(
                    f"DELETE FROM {table} WHERE owner_id = ? AND persona_id = ?",
                    (owner, persona),
                ).rowcount
        return counts

    def _encode(self, value: Mapping[str, Any], prefix: str, owner_id: str, persona_id: str) -> bytes:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self.encryption.encrypt(payload, f"{prefix}{owner_id}/{persona_id}".encode("utf-8"))

    def _decode(self, record_version: object, envelope: object, prefix: str, owner_id: str, persona_id: str) -> dict[str, Any]:
        if record_version != _RECORD_VERSION or not isinstance(envelope, bytes):
            raise LearningRepositoryError("learning_record_corrupt", "learning record is invalid")
        try:
            payload = self.encryption.decrypt(
                envelope,
                f"{prefix}{owner_id}/{persona_id}".encode("utf-8"),
            )
            value = json.loads(payload.decode("utf-8"))
        except (AuthenticationError, InvalidEncryptedPayloadError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LearningRepositoryError("learning_record_corrupt", "learning record is invalid") from exc
        if not isinstance(value, dict):
            raise LearningRepositoryError("learning_record_corrupt", "learning record is invalid")
        return value


def _upsert_aggregate(
    connection: Any,
    table: str,
    owner_id: str,
    persona_id: str,
    envelope: bytes,
    updated_at: str,
    *,
    extra_columns: str = "",
    extra_values: tuple[object, ...] = (),
) -> None:
    if extra_columns:
        update = f"record_version = ?, index_version = ?, encrypted_payload = ?, updated_at = ?"
        insert_columns = "id, owner_id, persona_id, record_version, index_version, encrypted_payload, updated_at"
        insert_values = (str(uuid4()), owner_id, persona_id, _RECORD_VERSION, *extra_values, envelope, updated_at)
        update_values = (_RECORD_VERSION, *extra_values, envelope, updated_at, owner_id, persona_id)
    else:
        update = "record_version = ?, encrypted_payload = ?, updated_at = ?"
        insert_columns = "id, owner_id, persona_id, record_version, encrypted_payload, updated_at"
        insert_values = (str(uuid4()), owner_id, persona_id, _RECORD_VERSION, envelope, updated_at)
        update_values = (_RECORD_VERSION, envelope, updated_at, owner_id, persona_id)
    updated = connection.execute(
        f"UPDATE {table} SET {update} WHERE owner_id = ? AND persona_id = ?",
        update_values,
    ).rowcount
    if updated == 0:
        connection.execute(
            f"INSERT INTO {table} ({insert_columns}) VALUES ({','.join('?' for _ in insert_values)})",
            insert_values,
        )


def _scope(owner_id: object, persona_id: object) -> tuple[str, str]:
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise ValueError("owner_id must be a non-empty string")
    if not isinstance(persona_id, str) or not persona_id.strip():
        raise ValueError("persona_id must be a non-empty string")
    return owner_id.strip(), persona_id.strip()


def _profile_from_dict(value: Mapping[str, Any]) -> StyleProfile:
    try:
        profile = StyleProfile(
            profile_version=_positive_int(value["profile_version"]),
            message_count=_positive_int(value["message_count"]),
            message_length=dict(_mapping(value["message_length"])),
            vocabulary=dict(_mapping(value["vocabulary"])),
            punctuation=dict(_mapping(value["punctuation"])),
            emoji=dict(_mapping(value["emoji"])),
            cadence=dict(_mapping(value["cadence"])),
            emotion_tendency=dict(_mapping(value["emotion_tendency"])),
            preferred_forms_of_address=tuple(dict(_mapping(item)) for item in value["preferred_forms_of_address"]),
            relationship_context=dict(_mapping(value["relationship_context"])),
            relationship_behavior=dict(_mapping(value["relationship_behavior"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LearningRepositoryError("learning_record_corrupt", "style profile record is invalid") from exc
    return profile


def _memory_from_dict(value: Mapping[str, Any]) -> LongTermMemory:
    try:
        candidates = tuple(_candidate_from_dict(item) for item in value["candidates"])
        return LongTermMemory(
            memory_version=_positive_int(value["memory_version"]),
            source_record_count=_nonnegative_int(value["source_record_count"]),
            accepted_record_count=_nonnegative_int(value["accepted_record_count"]),
            candidates=candidates,
            relationship_context=dict(_mapping(value["relationship_context"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LearningRepositoryError("learning_record_corrupt", "long-term memory record is invalid") from exc


def _candidate_from_dict(value: object) -> MemoryCandidate:
    if not isinstance(value, Mapping):
        raise TypeError("candidate must be an object")
    memory_id = value["memory_id"]
    if not isinstance(memory_id, str) or not _ID.fullmatch(memory_id):
        raise ValueError("memory_id is invalid")
    source_ids = value["source_record_ids"]
    if isinstance(source_ids, (str, bytes)):
        raise TypeError("source_record_ids must be a list")
    return MemoryCandidate(
        memory_id=memory_id,
        kind=_text(value["kind"]),
        text=_text(value["text"]),
        source_record_ids=tuple(_text(item) for item in source_ids),
        occurred_at=value.get("occurred_at") if value.get("occurred_at") is None else _text(value["occurred_at"]),
        confidence=float(value["confidence"]),
        review_state=_text(value["review_state"]),
        speaker_scope=_text(value["speaker_scope"]),
    )


def _vector_index_from_dict(value: Mapping[str, Any], index_version: object, memory: LongTermMemory) -> dict[str, tuple[str, ...]]:
    if index_version != _INDEX_VERSION or value.get("index_version") != _INDEX_VERSION:
        raise LearningRepositoryError("vector_index_corrupt", "learning vector index is invalid")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise LearningRepositoryError("vector_index_corrupt", "learning vector index is invalid")
    index: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("memory_id"), str):
            raise LearningRepositoryError("vector_index_corrupt", "learning vector index is invalid")
        tokens = entry.get("tokens")
        if not isinstance(tokens, list) or any(not isinstance(token, str) for token in tokens):
            raise LearningRepositoryError("vector_index_corrupt", "learning vector index is invalid")
        index[entry["memory_id"]] = tuple(tokens)
    expected = {candidate.memory_id for candidate in memory.candidates}
    if set(index) != expected:
        raise LearningRepositoryError("vector_index_corrupt", "learning vector index is invalid")
    return index


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("value must be an object")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("text must be non-empty")
    return value.strip()


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("value must be positive")
    return value


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("value must be non-negative")
    return value


__all__ = ["LearningRepository", "LearningRepositoryError"]
