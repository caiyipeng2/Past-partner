import unittest

from src.domain.usage_records import UsageStatus
from src.providers.base import ChatMessage, ChatRequest, ChatResponse
from src.providers.catalog import ProviderCatalog
from src.providers.testing import deterministic_test_provider_definition
from src.services.usage_service import UsageService


class _Repository:
    def __init__(self) -> None:
        self.records = []

    def append(self, record):
        self.records.append(record)
        return record

    def list(self, owner_id, *, limit=100, before=None):
        return [record for record in self.records if record.owner_id == owner_id]


class UsageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        default = ProviderCatalog.default()
        self.catalog = ProviderCatalog((*default.providers(), deterministic_test_provider_definition()))
        self.repository = _Repository()
        self.service = UsageService(self.repository, self.catalog)
        self.request = ChatRequest(
            provider_id="test",
            model_id="deterministic",
            messages=(ChatMessage(role="user", content="hello"),),
        )

    def test_records_priced_normalized_usage_without_raw_provider_response(self) -> None:
        record = self.service.record_chat(
            "owner-1",
            self.request,
            ChatResponse(
                provider_id="test",
                model_id="deterministic",
                content="reply",
                usage={"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
                provider_request_id="request-1",
            ),
        )

        self.assertEqual(UsageStatus.PRICED, record.status)
        self.assertEqual(12, record.input_tokens)
        self.assertEqual(8, record.output_tokens)
        self.assertNotIn("request-1", record.to_dict().__repr__())

    def test_missing_usage_is_explicit_and_has_no_charge(self) -> None:
        record = self.service.record_chat(
            "owner-1",
            self.request,
            ChatResponse(
                provider_id="test",
                model_id="deterministic",
                content="reply",
                usage=None,
                provider_request_id="request-2",
            ),
        )

        self.assertEqual(UsageStatus.USAGE_UNAVAILABLE, record.status)
        self.assertIsNone(record.platform_charge)


if __name__ == "__main__":
    unittest.main()
