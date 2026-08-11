import unittest

from src.learning.long_term_memory import LongTermMemoryExtractor
from src.learning.vector_retrieval import VectorMemoryRetriever, VectorRetrievalError
from src.domain.messages import NormalizedMessage


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


def _accepted_memory(messages: tuple[NormalizedMessage, ...]):
    memory = LongTermMemoryExtractor().extract(
        messages,
        persona_sender_ids={"persona"},
        user_sender_ids={"user"},
    )
    for candidate in memory.candidates:
        memory = memory.review(candidate.memory_id, "accepted")
    return memory


class VectorRetrievalTests(unittest.TestCase):
    def test_retrieves_only_accepted_memories_with_auditable_sources(self) -> None:
        memory = LongTermMemoryExtractor().extract(
            (
                _message("1" * 64, "persona", "我们第一次一起去杭州旅行，记得西湖边的那家店。", "2024-05-20T10:00:00+00:00"),
                _message("2" * 64, "persona", "我喜欢吃火锅。", "2024-05-21T10:00:00+00:00"),
            ),
            persona_sender_ids={"persona"},
        )
        accepted = memory.review(memory.candidates[0].memory_id, "accepted")

        result = VectorMemoryRetriever().retrieve(
            accepted,
            "西湖旅行",
            as_of="2024-05-25T10:00:00+00:00",
            max_candidates=5,
            max_tokens=100,
        )

        self.assertEqual(1, len(result.memories))
        self.assertEqual("accepted", result.memories[0].review_state)
        self.assertIn("1" * 64, result.memories[0].source_record_ids)
        self.assertGreater(result.memories[0].score, 0)
        self.assertNotIn("西湖旅行", str(result.to_dict()))
        self.assertEqual(64, len(result.query_fingerprint))

    def test_applies_token_and_candidate_budgets_deterministically(self) -> None:
        memory = _accepted_memory(
            (
                _message("3" * 64, "persona", "我喜欢周末去公园散步。", "2024-05-20T10:00:00+00:00"),
                _message("4" * 64, "persona", "我喜欢周末看电影。", "2024-05-21T10:00:00+00:00"),
            )
        )

        first = VectorMemoryRetriever().retrieve(memory, "周末喜欢", max_candidates=1, max_tokens=6)
        second = VectorMemoryRetriever().retrieve(memory, "周末喜欢", max_candidates=1, max_tokens=6)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertLessEqual(len(first.memories), 1)
        self.assertLessEqual(first.used_tokens, 6)

    def test_recency_and_speaker_scope_are_privacy_filters(self) -> None:
        memory = _accepted_memory(
            (
                _message("5" * 64, "persona", "我们去年一起旅行。", "2023-01-01T10:00:00+00:00"),
                _message("6" * 64, "unknown", "我们昨天一起旅行。", "2024-05-24T10:00:00+00:00"),
            )
        )

        result = VectorMemoryRetriever().retrieve(
            memory,
            "一起旅行",
            as_of="2024-05-25T10:00:00+00:00",
            max_age_days=30,
        )

        self.assertEqual((), result.memories)
        self.assertEqual(2, result.excluded_counts["outside_recency_budget"])
        self.assertEqual(2, result.excluded_counts["speaker_scope"])

    def test_empty_accepted_memory_is_safe_and_invalid_budget_fails_closed(self) -> None:
        memory = LongTermMemoryExtractor().extract(
            (_message("7" * 64, "persona", "她喜欢看电影。", "2024-05-20T10:00:00+00:00"),),
            persona_sender_ids={"persona"},
        )

        empty = VectorMemoryRetriever().retrieve(memory, "电影")
        self.assertEqual((), empty.memories)
        self.assertEqual(1, empty.excluded_counts["not_accepted"])

        with self.assertRaises(VectorRetrievalError) as query_error:
            VectorMemoryRetriever().retrieve(memory, "")
        self.assertEqual("query_required", query_error.exception.code)

        with self.assertRaises(VectorRetrievalError) as budget_error:
            VectorMemoryRetriever().retrieve(memory, "电影", max_tokens=0)
        self.assertEqual("invalid_token_budget", budget_error.exception.code)


if __name__ == "__main__":
    unittest.main()
