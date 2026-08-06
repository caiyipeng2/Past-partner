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

    def test_persona_edit_contract_uses_detail_load_and_patch(self) -> None:
        for field_id in (
            "relationshipLabel",
            "preferredAddress",
            "userAddress",
            "relationshipDescription",
            "toneBoundaries",
            "forbiddenTopics",
        ):
            with self.subTest(field_id=field_id):
                self.assertIn(f'id="{field_id}"', self.html)
        self.assertIn("loadExistingPersona", self.javascript)
        self.assertIn("method: 'PATCH'", self.javascript)
        self.assertIn("/personas/", self.javascript)
        self.assertIn("relationship_label", self.javascript)
        self.assertIn("tone_boundaries", self.javascript)

    def test_browser_can_resume_an_existing_import_job(self) -> None:
        self.assertIn("localStorage.getItem", self.javascript)
        self.assertIn("localStorage.setItem", self.javascript)
        self.assertIn("localStorage.removeItem", self.javascript)
        self.assertIn("resolveImportJob", self.javascript)
        self.assertIn("missing-chunks", self.javascript)
        self.assertIn("/progress", self.javascript)

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

    def test_workspace_uses_the_modern_workbench_visual_contract(self) -> None:
        for class_name in (
            "topbar-meta",
            "brand-eyebrow",
            "optional-fields",
            "chat-header-copy",
            "composer-head",
        ):
            with self.subTest(class_name=class_name):
                self.assertIn(f'class="{class_name}', self.html)
        for token in ("--accent", "--shadow-soft", "--radius-md", "prefers-reduced-motion"):
            with self.subTest(token=token):
                self.assertIn(token, self.styles)
        self.assertNotIn("linear-gradient", self.styles)


if __name__ == "__main__":
    unittest.main()
