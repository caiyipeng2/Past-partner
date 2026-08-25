"""Documentation contract for the R1-04 external worker slice."""

from __future__ import annotations

from pathlib import Path
import unittest


class WorkerDocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path.cwd()
        cls.readme = (cls.root / "README.md").read_text(encoding="utf-8")
        cls.privacy = (cls.root / "docs" / "privacy_policy.md").read_text(encoding="utf-8")
        cls.roadmap = (cls.root / "docs" / "ROADMAP.md").read_text(encoding="utf-8")

    def test_documents_expose_external_worker_command_and_shared_boundary(self) -> None:
        for content in (self.readme, self.privacy, self.roadmap):
            with self.subTest(document=content[:20]):
                self.assertIn("python -m src.worker", content)
                self.assertIn("companion-worker", content)
        for content in (self.readme, self.privacy):
            self.assertIn("PAST_PARTNER_METADATA_DSN", content)

    def test_documents_do_not_claim_future_operations_are_complete(self) -> None:
        for content in (self.readme, self.privacy, self.roadmap):
            with self.subTest(document=content[:20]):
                self.assertIn("SIEM", content)
                self.assertIn("broker", content)
                self.assertTrue(
                    "仍未实现" in content or "仍不提供" in content or "仍待后续" in content
                )


if __name__ == "__main__":
    unittest.main()
