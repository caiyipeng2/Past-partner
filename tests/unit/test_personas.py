import unittest

from src.domain.messages import NormalizedMessage
from src.domain.personas import Persona, PersonaValidationError, RelationshipType


class PersonaTests(unittest.TestCase):
    def test_accepts_every_supported_relationship(self) -> None:
        for relationship in (
            RelationshipType.FATHER,
            RelationshipType.MOTHER,
            RelationshipType.RELATIVE,
            RelationshipType.FRIEND,
            RelationshipType.PARTNER,
        ):
            with self.subTest(relationship=relationship):
                persona = Persona.create("重要的人", relationship.value)
                self.assertEqual(relationship, persona.relationship_type)
                self.assertIsNone(persona.custom_label)

    def test_custom_relationship_requires_a_label(self) -> None:
        with self.assertRaises(PersonaValidationError):
            Persona.create("重要的人", RelationshipType.CUSTOM.value)

        persona = Persona.create(
            "重要的人",
            RelationshipType.CUSTOM.value,
            custom_label="导师",
        )
        self.assertEqual("导师", persona.custom_label)

    def test_rejects_unknown_relationship(self) -> None:
        with self.assertRaises(PersonaValidationError):
            Persona.create("重要的人", "coworker")

    def test_rejects_unbounded_or_control_character_identity_text(self) -> None:
        invalid_cases = (
            {"display_name": "a" * 81, "relationship_type": "friend"},
            {"display_name": "名字\n伪造", "relationship_type": "friend"},
            {
                "display_name": "重要的人",
                "relationship_type": "custom",
                "custom_label": "a" * 41,
            },
            {
                "display_name": "重要的人",
                "relationship_type": "custom",
                "custom_label": "导师\t异常",
            },
        )

        for values in invalid_cases:
            with self.subTest(values=values):
                with self.assertRaises(PersonaValidationError):
                    Persona.create(**values)

    def test_normalizes_message_aliases_to_one_contract(self) -> None:
        message = NormalizedMessage.from_mapping(
            {
                "sender": "wxid_123",
                "sender_name": "小雨",
                "message": "晚安",
                "time": "2026-07-30T22:30:00+08:00",
                "type": "text",
            }
        )

        self.assertEqual("wxid_123", message.sender_id)
        self.assertEqual("小雨", message.sender_name)
        self.assertEqual("晚安", message.content)
        self.assertEqual("2026-07-30T22:30:00+08:00", message.timestamp)
        self.assertEqual("text", message.message_type)
        self.assertEqual((), message.attachments)

    def test_normalizes_content_field_and_attachments(self) -> None:
        message = NormalizedMessage.from_mapping(
            {
                "sender_id": "qq_42",
                "content": "看这张图",
                "timestamp": "2026-07-30T20:00:00+08:00",
                "message_type": "image",
                "attachments": [{"name": "photo.jpg", "media_type": "image/jpeg"}],
            }
        )

        self.assertEqual("看这张图", message.content)
        self.assertEqual("image", message.message_type)
        self.assertEqual("photo.jpg", message.attachments[0]["name"])


if __name__ == "__main__":
    unittest.main()
