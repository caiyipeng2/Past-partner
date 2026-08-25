"""Integration-level boundary checks for the R1-04 broker contract slice."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class BrokerContractDocumentationTests(unittest.TestCase):
    def test_documents_describe_outbox_redaction_and_future_production_adapter(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        privacy = (ROOT / "docs" / "privacy_policy.md").read_text(encoding="utf-8")
        roadmap = (ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
        for content in (readme, privacy):
            self.assertIn("outbox", content)
            self.assertIn("Redis", content)
            self.assertIn("RabbitMQ", content)
            self.assertIn("仍未", content)
        self.assertIn("broker 契约切片", roadmap)

    def test_documents_do_not_claim_a_specific_production_broker(self) -> None:
        for name in ("README.md", "docs/privacy_policy.md", "docs/ROADMAP.md"):
            content = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("Redis 已接入", content)
            self.assertNotIn("RabbitMQ 已接入", content)
            self.assertNotIn("broker 密钥已配置", content)


if __name__ == "__main__":
    unittest.main()
