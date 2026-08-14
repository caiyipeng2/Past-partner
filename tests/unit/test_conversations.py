import unittest

from src.domain.conversations import ConversationValidationError, ConversationMessage


class ConversationDomainTests(unittest.TestCase):
    def test_message_accepts_only_bounded_user_or_assistant_content(self) -> None:
        message = ConversationMessage.create(role="user", content="你好")
        self.assertEqual("user", message.role)
        self.assertEqual("你好", message.content)
        with self.assertRaises(ConversationValidationError) as captured:
            ConversationMessage.create(role="system", content="hidden")
        self.assertEqual("invalid_message_role", captured.exception.code)

    def test_empty_or_oversized_content_is_rejected(self) -> None:
        with self.assertRaises(ConversationValidationError) as captured:
            ConversationMessage.create(role="user", content="  ")
        self.assertEqual("empty_message", captured.exception.code)
        with self.assertRaises(ConversationValidationError) as captured:
            ConversationMessage.create(role="user", content="x" * 20_001)
        self.assertEqual("message_too_large", captured.exception.code)
