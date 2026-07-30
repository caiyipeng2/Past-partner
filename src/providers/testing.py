"""Deterministic adapter that can never be enabled outside test mode."""

from src.providers.base import ChatRequest, ChatResponse


class DeterministicTestAdapter:
    provider_id = "test"

    def supports_model(self, model_id: str) -> bool:
        return model_id == "deterministic"

    def chat(self, request: ChatRequest) -> ChatResponse:
        last_message = request.messages[-1].content if request.messages else ""
        return ChatResponse(
            provider_id=self.provider_id,
            model_id=request.model_id,
            content=f"测试回复：{last_message}",
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            provider_request_id="test-request",
        )
