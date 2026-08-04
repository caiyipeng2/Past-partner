import re
import unittest
from pathlib import Path


class WebContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (Path.cwd() / "web" / "workspace.html").read_text(encoding="utf-8")
        cls.javascript = (Path.cwd() / "web" / "workspace.js").read_text(encoding="utf-8")
        cls.styles = (Path.cwd() / "web" / "workspace.css").read_text(encoding="utf-8")

    def test_relationship_choices_are_present_before_file_selection(self) -> None:
        values = set(re.findall(r'name="relationship_type"\s+value="([^"]+)"', self.html))
        self.assertEqual({"father", "mother", "relative", "friend", "partner", "custom"}, values)
        self.assertLess(self.html.index('id="personaForm"'), self.html.index('id="chatFile"'))

    def test_client_uses_same_origin_versioned_api(self) -> None:
        self.assertIn("const API_BASE = '/api/v1';", self.javascript)
        self.assertNotIn("http://localhost", self.javascript)
        self.assertNotIn("http://127.0.0.1", self.javascript)

    def test_import_contract_includes_persona_and_chunk_integrity(self) -> None:
        self.assertRegex(self.javascript, r"persona_id:\s*state\.personaId")
        self.assertIn("X-Chunk-Sha256", self.javascript)
        self.assertIn("/chunks/", self.javascript)
        self.assertIn("/complete", self.javascript)

    def test_browser_can_resume_an_existing_import_job(self) -> None:
        self.assertIn("localStorage.getItem", self.javascript)
        self.assertIn("localStorage.setItem", self.javascript)
        self.assertIn("localStorage.removeItem", self.javascript)
        self.assertIn("resolveImportJob", self.javascript)
        self.assertIn("missing-chunks", self.javascript)

    def test_browser_exposes_pause_and_resume_controls(self) -> None:
        self.assertIn('id="pauseUploadButton"', self.html)
        self.assertIn("AbortController", self.javascript)
        self.assertIn("toggleUploadPause", self.javascript)

    def test_browser_exposes_import_cancellation(self) -> None:
        self.assertIn('id="cancelUploadButton"', self.html)
        self.assertIn("cancelUpload", self.javascript)
        self.assertIn("/cancel", self.javascript)

    def test_untrusted_text_is_never_rendered_with_inner_html(self) -> None:
        self.assertNotIn("innerHTML", self.javascript)
        self.assertIn("textContent", self.javascript)

    def test_layout_has_a_mobile_breakpoint_and_visible_focus(self) -> None:
        self.assertIn("@media (max-width: 760px)", self.styles)
        self.assertIn(":focus-visible", self.styles)


if __name__ == "__main__":
    unittest.main()
