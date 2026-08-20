"""Conversation orchestration between personas, encrypted history, and providers."""

from __future__ import annotations

from src.domain.conversations import Conversation, ConversationValidationError
from src.providers.base import ChatMessage, ChatRequest
from src.providers.gateway import ProviderGateway
from src.services.conversation_repository import ConversationRepository
from src.services.persona_service import PersonaService
from src.services.usage_service import UsageService, UsageServiceError


class ConversationNotFoundError(LookupError):
    pass


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository,
        personas: PersonaService,
        gateway: ProviderGateway,
        usage: UsageService | None = None,
    ) -> None:
        self.repository = repository
        self.personas = personas
        self.gateway = gateway
        self.usage = usage

    def create(self, owner_id: str, persona_id: str, provider_id: str, model_id: str) -> Conversation:
        self.personas.get(owner_id, persona_id)
        conversation = Conversation.create(
            persona_id=persona_id,
            provider_id=provider_id,
            model_id=model_id,
        )
        return self.repository.save(owner_id, conversation)

    def get(self, owner_id: str, conversation_id: str) -> Conversation:
        conversation = self.repository.get(owner_id, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    def list(self, owner_id: str, persona_id: str | None = None) -> list[Conversation]:
        return self.repository.list(owner_id, persona_id)

    def send(self, owner_id: str, conversation_id: str, content: str) -> Conversation:
        conversation = self.get(owner_id, conversation_id)
        if not isinstance(content, str) or not content.strip():
            raise ConversationValidationError("empty_message", "message content must not be empty")
        self.personas.get(owner_id, conversation.persona_id)
        request = ChatRequest(
            provider_id=conversation.provider_id,
            model_id=conversation.model_id,
            messages=tuple(
                ChatMessage(role=message.role, content=message.content)
                for message in conversation.messages
            )
            + (ChatMessage(role="user", content=content),),
        )
        response = self.gateway.chat(request)
        if not isinstance(response.content, str) or not response.content.strip():
            raise ConversationValidationError("empty_provider_response", "provider returned an empty response")
        if self.usage is not None:
            try:
                self.usage.record_chat(owner_id, request, response)
            except UsageServiceError:
                # Provider usage is auxiliary to the already successful chat. The
                # ledger never fabricates a charge or turns a provider success into
                # a retry that could double-spend. The read API exposes only records
                # that were durably accepted by the repository.
                pass
        updated = conversation.add_user_and_assistant(content, response.content)
        stored = self.repository.replace(owner_id, updated)
        if stored is None:
            raise ConversationNotFoundError(conversation_id)
        return stored

    def delete_for_persona(self, owner_id: str, persona_id: str) -> int:
        return self.repository.delete_for_persona(owner_id, persona_id)
