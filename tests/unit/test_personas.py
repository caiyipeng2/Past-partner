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

    def test_persists_full_relationship_context_and_schema_version(self) -> None:
        persona = Persona.create(
            "小雨",
            RelationshipType.FRIEND.value,
            preferred_address="你",
            user_address="小雨",
            relationship_description="大学时期认识的朋友",
            tone_boundaries=("温和", "不说教"),
            forbidden_topics=("未公开的家庭隐私", "财务密码"),
        )

        payload = persona.to_dict()
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual(persona.created_at, persona.updated_at)
        self.assertEqual("你", payload["preferred_address"])
        self.assertEqual("小雨", payload["user_address"])
        self.assertEqual(["温和", "不说教"], payload["tone_boundaries"])
        self.assertEqual(["未公开的家庭隐私", "财务密码"], payload["forbidden_topics"])
        self.assertEqual(persona, Persona.from_dict(payload))

    def test_accepts_relationship_label_alias_and_rejects_invalid_schema_metadata(self) -> None:
        persona = Persona.create(
            "重要的人",
            RelationshipType.CUSTOM.value,
            relationship_label="导师",
        )
        self.assertEqual("导师", persona.custom_label)
        self.assertEqual("导师", persona.to_dict()["relationship_label"])

        with self.assertRaises(PersonaValidationError):
            Persona.create(
                "小雨",
                RelationshipType.FRIEND.value,
                tone_boundaries="温和",
            )
        with self.assertRaises(PersonaValidationError):
            Persona.from_dict({**persona.to_dict(), "schema_version": 2})
        with self.assertRaises(PersonaValidationError):
            Persona.from_dict({**persona.to_dict(), "custom_label": "另一身份"})

    def test_loads_legacy_payload_with_defaults_for_new_fields(self) -> None:
        legacy = {
            "id": "persona-legacy",
            "display_name": "旧记录",
            "relationship_type": "friend",
            "custom_label": None,
            "created_at": "2026-07-30T12:00:00+00:00",
        }

        persona = Persona.from_dict(legacy)

        self.assertEqual(1, persona.schema_version)
        self.assertEqual(persona.created_at, persona.updated_at)
        self.assertIsNone(persona.preferred_address)
        self.assertEqual((), persona.forbidden_topics)

    def test_updates_partial_relationship_context_without_changing_identity(self) -> None:
        persona = Persona.create(
            "小雨",
            RelationshipType.FRIEND.value,
            preferred_address="你",
            relationship_description="大学同学",
        )

        updated = persona.update(
            {
                "display_name": "小雨同学",
                "user_address": "小雨",
                "forbidden_topics": ["家庭隐私"],
            }
        )

        self.assertEqual(persona.id, updated.id)
        self.assertEqual(persona.created_at, updated.created_at)
        self.assertEqual("小雨同学", updated.display_name)
        self.assertEqual("你", updated.preferred_address)
        self.assertEqual("小雨", updated.user_address)
        self.assertEqual(("家庭隐私",), updated.forbidden_topics)
        self.assertEqual(1, updated.schema_version)

    def test_rejects_unknown_or_immutable_persona_update_fields(self) -> None:
        persona = Persona.create("小雨", RelationshipType.FRIEND.value)

        with self.assertRaises(PersonaValidationError):
            persona.update({"unknown": "value"})
        with self.assertRaises(PersonaValidationError):
            persona.update({"id": "replacement"})

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

    def test_preserves_an_explicit_stable_record_id(self) -> None:
        record_id = "a" * 64
        message = NormalizedMessage.from_mapping(
            {
                "record_id": record_id,
                "sender_id": "wxid_123",
                "content": "带稳定 ID",
                "timestamp": "2026-07-30T20:00:00+08:00",
            }
        )

        self.assertEqual(record_id, message.record_id)
        self.assertEqual(record_id, message.to_dict()["record_id"])


if __name__ == "__main__":
    unittest.main()
