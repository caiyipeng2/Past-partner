import unittest

from src.domain.messages import NormalizedMessage
from src.learning.long_term_memory import LongTermMemoryError, LongTermMemoryExtractor
from src.preprocessing.data_parser import ChatDataParser
from src.preprocessing.parser_registry import ParseResult


def _message(record_id: str, sender_id: str, content: str, timestamp: str) -> NormalizedMessage:
    return NormalizedMessage.from_mapping(
        {
            "record_id": record_id,
            "sender_id": sender_id,
            "sender_name": sender_id,
            "content": content,
            "timestamp": timestamp,
            "message_type": "text",
        }
    )


class LongTermMemoryTests(unittest.TestCase):
    def test_extracts_evidence_bound_candidates_from_accepted_records(self) -> None:
        records = (
            _message("1" * 64, "persona", "我们第一次一起去杭州旅行，记得西湖边的那家店。", "2024-05-20T10:00:00+00:00"),
            _message("2" * 64, "user", "我喜欢吃火锅，也不喜欢香菜。", "2024-05-21T10:00:00+00:00"),
            _message("3" * 64, "persona", "下周六一起看电影吧！", "2024-05-25T10:00:00+00:00"),
            _message("4" * 64, "user", "我们是情侣，周末常见面。", "2024-05-26T10:00:00+00:00"),
        )

        memory = LongTermMemoryExtractor().extract(
            records,
            accepted_record_ids={"1" * 64, "3" * 64, "4" * 64},
            persona_sender_ids={"persona"},
            user_sender_ids={"user"},
            relationship_type="partner",
            relationship_label="情侣",
        )
        payload = memory.to_dict()
        kinds = {candidate["kind"] for candidate in payload["candidates"]}

        self.assertIn("event", kinds)
        self.assertIn("timeline", kinds)
        self.assertIn("relationship", kinds)
        self.assertNotIn("喜欢吃火锅", str(payload))
        self.assertTrue(all(candidate["review_state"] == "needs_review" for candidate in payload["candidates"]))
        self.assertTrue(all(candidate["source_record_ids"] for candidate in payload["candidates"]))
        self.assertEqual("partner", payload["relationship_context"]["relationship_type"])

    def test_reviews_a_candidate_without_mutating_original_memory(self) -> None:
        record_id = "a" * 64
        memory = LongTermMemoryExtractor().extract(
            (_message(record_id, "persona", "我们一起去看电影。", "2024-05-20T10:00:00+00:00"),),
            persona_sender_ids={"persona"},
        )
        candidate_id = memory.to_dict()["candidates"][0]["memory_id"]

        reviewed = memory.review(candidate_id, "accepted")

        self.assertEqual("needs_review", memory.to_dict()["candidates"][0]["review_state"])
        self.assertEqual("accepted", reviewed.to_dict()["candidates"][0]["review_state"])

    def test_extracts_facts_and_deduplicates_evidence(self) -> None:
        repeated = (
            _message("e" * 64, "persona", "她住在上海。", "2024-05-20T10:00:00+00:00"),
            _message("f" * 64, "persona", "她住在上海。", "2024-05-21T10:00:00+00:00"),
        )

        payload = LongTermMemoryExtractor().extract(
            repeated,
            persona_sender_ids={"persona"},
        ).to_dict()

        self.assertEqual(1, payload["candidate_count"])
        self.assertEqual("fact", payload["candidates"][0]["kind"])
        self.assertEqual(2, len(payload["candidates"][0]["source_record_ids"]))

    def test_rejects_unbounded_candidates_and_invalid_review_state(self) -> None:
        records = (
            _message("1" * 64, "persona", "我们一起旅行。", "2024-05-20T10:00:00+00:00"),
        )
        with self.assertRaises(LongTermMemoryError) as limit_error:
            LongTermMemoryExtractor().extract(records, persona_sender_ids={"persona"}, max_candidates=1)
        self.assertEqual("candidate_limit_exceeded", limit_error.exception.code)

        memory = LongTermMemoryExtractor().extract(records, persona_sender_ids={"persona"})
        candidate_id = memory.to_dict()["candidates"][0]["memory_id"]
        with self.assertRaises(LongTermMemoryError) as review_error:
            memory.review(candidate_id, "published")
        self.assertEqual("invalid_review_state", review_error.exception.code)

    def test_rejects_missing_evidence_and_unknown_accepted_records(self) -> None:
        without_id = NormalizedMessage.from_mapping(
            {
                "sender_id": "persona",
                "content": "我们一起旅行。",
                "timestamp": "2024-05-20T10:00:00+00:00",
                "message_type": "text",
            }
        )
        with self.assertRaises(LongTermMemoryError) as missing_id:
            LongTermMemoryExtractor().extract((without_id,), persona_sender_ids={"persona"})
        self.assertEqual("record_id_required", missing_id.exception.code)

        with self.assertRaises(LongTermMemoryError) as unknown_id:
            LongTermMemoryExtractor().extract(
                (_message("b" * 64, "persona", "我们一起旅行。", "2024-05-20T10:00:00+00:00"),),
                accepted_record_ids={"c" * 64},
                persona_sender_ids={"persona"},
            )
        self.assertEqual("unknown_accepted_record", unknown_id.exception.code)

        with self.assertRaises(LongTermMemoryError) as invalid_id:
            LongTermMemoryExtractor().extract(
                (_message("b" * 64, "persona", "我们一起旅行。", "2024-05-20T10:00:00+00:00"),),
                accepted_record_ids={"not-a-record-id"},
                persona_sender_ids={"persona"},
            )
        self.assertEqual("invalid_record_ids", invalid_id.exception.code)

    def test_parser_facade_uses_canonical_records_for_memory_candidates(self) -> None:
        records = (_message("d" * 64, "persona", "她喜欢看电影。", "2024-05-20T10:00:00+00:00"),)

        class StubRegistry:
            def parse(self, path, metadata):
                return ParseResult("generic_text", records, (), {"record_count": 1})

        memory = ChatDataParser(StubRegistry()).generate_long_term_memory(
            "ignored.txt",
            persona_sender_ids={"persona"},
        )

        self.assertEqual("generic_text", memory["source_type"])
        self.assertEqual(1, memory["candidate_count"])
        self.assertEqual("preference", memory["candidates"][0]["kind"])

    def test_bounds_timeline_evidence_text(self) -> None:
        long_content = "一起旅行：" + ("很长的回忆。" * 60)
        memory = LongTermMemoryExtractor().extract(
            (_message("9" * 64, "persona", long_content, "2024-05-20T10:00:00+00:00"),),
            persona_sender_ids={"persona"},
        )

        self.assertTrue(all(len(candidate["text"]) <= 240 for candidate in memory.to_dict()["candidates"]))


if __name__ == "__main__":
    unittest.main()
