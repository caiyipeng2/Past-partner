"""Persona-scoped orchestration for persistent learning state."""

from __future__ import annotations

from src.learning.long_term_memory import LongTermMemory
from src.learning.style_profile import StyleProfile
from src.learning.vector_retrieval import MemoryRetrievalResult, VectorRetrievalError
from src.services.learning_repository import LearningRepository, LearningRepositoryError
from src.services.persona_service import PersonaNotFoundError, PersonaService


class LearningServiceError(RuntimeError):
    """Stable service boundary error without exposing repository details."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class LearningService:
    def __init__(self, repository: LearningRepository, personas: PersonaService):
        self.repository = repository
        self.personas = personas

    def save_style_profile(self, owner_id: str, persona_id: str, profile: StyleProfile) -> StyleProfile:
        self._require_persona(owner_id, persona_id)
        try:
            return self.repository.save_style_profile(owner_id, persona_id, profile)
        except LearningRepositoryError as exc:
            raise LearningServiceError(exc.code, str(exc)) from exc

    def save_style_profile_payload(self, owner_id: str, persona_id: str, value: object) -> StyleProfile:
        try:
            profile = self.repository.parse_style_profile(value)
        except LearningRepositoryError as exc:
            raise LearningServiceError(exc.code, str(exc)) from exc
        return self.save_style_profile(owner_id, persona_id, profile)

    def get_style_profile(self, owner_id: str, persona_id: str) -> StyleProfile:
        self._require_persona(owner_id, persona_id)
        try:
            profile = self.repository.get_style_profile(owner_id, persona_id)
        except LearningRepositoryError as exc:
            raise LearningServiceError(exc.code, str(exc)) from exc
        if profile is None:
            raise LearningServiceError("learning_not_found", "style profile does not exist")
        return profile

    def save_memory(self, owner_id: str, persona_id: str, memory: LongTermMemory) -> LongTermMemory:
        self._require_persona(owner_id, persona_id)
        try:
            return self.repository.save_memory(owner_id, persona_id, memory)
        except LearningRepositoryError as exc:
            raise LearningServiceError(exc.code, str(exc)) from exc

    def save_memory_payload(self, owner_id: str, persona_id: str, value: object) -> LongTermMemory:
        try:
            memory = self.repository.parse_memory(value)
        except LearningRepositoryError as exc:
            raise LearningServiceError(exc.code, str(exc)) from exc
        return self.save_memory(owner_id, persona_id, memory)

    def get_memory(self, owner_id: str, persona_id: str) -> LongTermMemory:
        self._require_persona(owner_id, persona_id)
        try:
            memory = self.repository.get_memory(owner_id, persona_id)
        except LearningRepositoryError as exc:
            raise LearningServiceError(exc.code, str(exc)) from exc
        if memory is None:
            raise LearningServiceError("learning_not_found", "long-term memory does not exist")
        return memory

    def review_memory(
        self,
        owner_id: str,
        persona_id: str,
        memory_id: str,
        review_state: str,
    ) -> LongTermMemory:
        self._require_persona(owner_id, persona_id)
        try:
            return self.repository.review_memory(owner_id, persona_id, memory_id, review_state)
        except LearningRepositoryError as exc:
            raise LearningServiceError(exc.code, str(exc)) from exc

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
        self._require_persona(owner_id, persona_id)
        try:
            return self.repository.retrieve(
                owner_id,
                persona_id,
                query,
                as_of=as_of,
                max_candidates=max_candidates,
                max_tokens=max_tokens,
                max_age_days=max_age_days,
                allowed_speaker_scopes=allowed_speaker_scopes,
            )
        except (LearningRepositoryError, VectorRetrievalError) as exc:
            code = getattr(exc, "code", "learning_retrieval_invalid")
            raise LearningServiceError(code, str(exc)) from exc

    def delete_for_persona(self, owner_id: str, persona_id: str) -> dict[str, int]:
        self._require_persona(owner_id, persona_id)
        try:
            return self.repository.delete_for_persona(owner_id, persona_id)
        except LearningRepositoryError as exc:
            raise LearningServiceError(exc.code, str(exc)) from exc

    def _require_persona(self, owner_id: str, persona_id: str) -> None:
        try:
            self.personas.get(owner_id, persona_id)
        except PersonaNotFoundError as exc:
            raise LearningServiceError("persona_not_found", "persona does not exist") from exc


__all__ = ["LearningService", "LearningServiceError"]
