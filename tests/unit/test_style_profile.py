import unittest

from src.domain.messages import NormalizedMessage
from src.preprocessing.data_parser import ChatDataParser
from src.preprocessing.parser_registry import ParseResult
from src.learning.style_profile import StyleProfileError, StyleProfileExtractor


def _message(sender_id: str, content: str, timestamp: str) -> NormalizedMessage:
    return NormalizedMessage.from_mapping(
        {
            "sender_id": sender_id,
            "sender_name": sender_id,
            "content": content,
            "timestamp": timestamp,
            "message_type": "text",
        }
    )


class StyleProfileTests(unittest.TestCase):
    def test_extracts_only_persona_messages_and_normalizes_style_dimensions(self) -> None:
        records = (
            _message("persona", "宝贝，你好呀！😊", "2026-08-10T10:00:00+00:00"),
            _message("user", "你好", "2026-08-10T10:01:00+00:00"),
            _message("persona", "今天真开心呀？😊", "2026-08-10T10:05:00+00:00"),
            _message("persona", "宝贝，晚安。", "2026-08-10T10:15:00+00:00"),
        )

        profile = StyleProfileExtractor().extract(
            records,
            persona_sender_ids={"persona"},
            user_sender_ids={"user"},
            known_addresses=("宝贝",),
            relationship_type="partner",
            relationship_label="情侣",
        )
        payload = profile.to_dict()

        self.assertEqual(3, payload["message_count"])
        self.assertEqual(3, payload["relationship_behavior"]["persona_message_count"])
        self.assertEqual(2, payload["emoji"]["message_count"])
        self.assertEqual(2, payload["emoji"]["counts"]["😊"])
        self.assertEqual(1.0, payload["punctuation"]["message_usage_rate"])
        self.assertEqual(3, payload["cadence"]["timestamp_count"])
        self.assertEqual(450.0, payload["cadence"]["average_interval_seconds"])
        self.assertEqual(2, payload["preferred_forms_of_address"][0]["count"])
        self.assertEqual("partner", payload["relationship_context"]["relationship_type"])
        self.assertEqual(4, payload["relationship_behavior"]["source_message_count"])
        self.assertNotIn("content", payload)
        self.assertNotIn("宝贝，你好呀", str(payload))

    def test_requires_at_least_one_persona_authored_message(self) -> None:
        with self.assertRaises(StyleProfileError) as captured:
            StyleProfileExtractor().extract(
                (_message("user", "你好", "2026-08-10T10:00:00+00:00"),),
                persona_sender_ids={"persona"},
            )

        self.assertEqual("persona_messages_required", captured.exception.code)

    def test_rejects_invalid_relationship_metadata(self) -> None:
        with self.assertRaises(StyleProfileError) as captured:
            StyleProfileExtractor().extract(
                (_message("persona", "你好", "2026-08-10T10:00:00+00:00"),),
                persona_sender_ids={"persona"},
                relationship_type="not-a-relationship",
            )

        self.assertEqual("invalid_relationship", captured.exception.code)

    def test_normalizes_preferred_address_without_retaining_message_text(self) -> None:
        profile = StyleProfileExtractor().extract(
            (_message("persona", "宝贝，晚安。", "2026-08-10T10:00:00+00:00"),),
            persona_sender_ids={"persona"},
            preferred_address="  宝贝  ",
            relationship_type="partner",
        ).to_dict()

        self.assertEqual("宝贝", profile["relationship_context"]["preferred_address"])
        self.assertEqual(1, profile["preferred_forms_of_address"][0]["count"])

    def test_chat_data_parser_uses_canonical_registry_records(self) -> None:
        records = (_message("persona", "你好呀！", "2026-08-10T10:00:00+00:00"),)

        class StubRegistry:
            def parse(self, path, metadata):
                return ParseResult("generic_text", records, (), {"record_count": 1})

        profile = ChatDataParser(StubRegistry()).generate_style_profile(
            "ignored.txt",
            persona_sender_ids={"persona"},
        )

        self.assertEqual(1, profile["message_count"])
        self.assertEqual("generic_text", profile["source_type"])


if __name__ == "__main__":
    unittest.main()
