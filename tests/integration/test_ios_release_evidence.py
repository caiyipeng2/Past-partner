from __future__ import annotations

import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "collect_ios_release_evidence.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ios-release.yml"


class IosReleaseEvidenceContractTests(unittest.TestCase):
    def _create_project(self, root: Path) -> None:
        (root / "mobile" / "ios" / "Runner").mkdir(parents=True)
        (root / "mobile" / "pubspec.yaml").write_text(
            "name: past_partner\nversion: 0.1.0+1\n", encoding="utf-8"
        )
        (root / "mobile" / "ios" / "Runner" / "Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "com.pastpartner.mobile",
                    "CFBundleShortVersionString": "0.1.0",
                    "CFBundleVersion": "1",
                }
            )
        )

    @staticmethod
    def _app_info(bundle_id: str = "com.pastpartner.mobile") -> dict[str, str]:
        return {
            "CFBundleIdentifier": bundle_id,
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1",
        }

    def _create_archive(self, path: Path, app_info: dict[str, str] | None = None) -> None:
        app = path / "Products" / "Applications" / "Past Partner.app"
        app.mkdir(parents=True)
        (path / "Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "ApplicationProperties": {
                        "CFBundleIdentifier": (app_info or self._app_info())["CFBundleIdentifier"]
                    }
                }
            )
        )
        (app / "Info.plist").write_bytes(plistlib.dumps(app_info or self._app_info()))

    def _create_ipa(self, path: Path, app_info: dict[str, str] | None = None) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "Payload/Past Partner.app/Info.plist",
                plistlib.dumps(app_info or self._app_info()),
            )

    def _run(self, root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--project-root", str(root), *arguments],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def test_valid_archive_and_ipa_emit_redacted_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="past-partner-ios-evidence-") as raw_root:
            root = Path(raw_root)
            self._create_project(root)
            archive = root / "build" / "Runner.xcarchive"
            ipa = root / "build" / "Past Partner.ipa"
            archive.parent.mkdir(parents=True)
            self._create_archive(archive)
            self._create_ipa(ipa)
            result = self._run(
                root,
                "--archive",
                str(archive),
                "--ipa",
                str(ipa),
            )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("ok", payload["status"])
        self.assertEqual({"name": "0.1.0", "code": "1"}, payload["version"])
        self.assertEqual("com.pastpartner.mobile", payload["bundle_id"])
        self.assertTrue(payload["artifacts"]["archive"]["present"])
        self.assertTrue(payload["artifacts"]["ipa"]["present"])
        self.assertGreater(payload["artifacts"]["archive"]["size_bytes"], 0)
        self.assertGreater(payload["artifacts"]["ipa"]["size_bytes"], 0)
        self.assertEqual("unsigned", payload["artifacts"]["archive"]["signing"])
        self.assertEqual("unsigned", payload["artifacts"]["ipa"]["signing"])
        self.assertNotIn(str(root), result.stdout)
        self.assertNotIn("PRIVATE KEY", result.stdout)

    def test_artifact_version_mismatch_is_stable_and_path_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="past-partner-ios-mismatch-") as raw_root:
            root = Path(raw_root)
            self._create_project(root)
            archive = root / "Runner.xcarchive"
            self._create_archive(
                archive,
                {
                    **self._app_info(),
                    "CFBundleShortVersionString": "9.9.9",
                },
            )
            result = self._run(root, "--archive", str(archive))

        self.assertEqual(1, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("artifact_version_mismatch", payload["code"])
        self.assertNotIn(str(root), result.stdout)

    def test_code_signature_trace_is_reported_as_signed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="past-partner-ios-signed-") as raw_root:
            root = Path(raw_root)
            self._create_project(root)
            archive = root / "Runner.xcarchive"
            ipa = root / "Past Partner.ipa"
            self._create_archive(archive)
            app = archive / "Products" / "Applications" / "Past Partner.app"
            (app / "_CodeSignature").mkdir()
            (app / "_CodeSignature" / "CodeResources").write_bytes(b"fixture")
            with zipfile.ZipFile(ipa, "w", compression=zipfile.ZIP_DEFLATED) as package:
                package.writestr(
                    "Payload/Past Partner.app/Info.plist",
                    plistlib.dumps(self._app_info()),
                )
                package.writestr("Payload/Past Partner.app/_CodeSignature/CodeResources", b"fixture")
            result = self._run(root, "--archive", str(archive), "--ipa", str(ipa))

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("signed", payload["artifacts"]["archive"]["signing"])
        self.assertEqual("signed", payload["artifacts"]["ipa"]["signing"])

    def test_archive_root_metadata_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="past-partner-ios-archive-metadata-") as raw_root:
            root = Path(raw_root)
            self._create_project(root)
            archive = root / "Runner.xcarchive"
            self._create_archive(archive)
            (archive / "Info.plist").write_bytes(
                plistlib.dumps(
                    {
                        "ApplicationProperties": {
                            "CFBundleIdentifier": "com.pastpartner.mobile",
                            "CFBundleShortVersionString": "0.1.0",
                            "CFBundleVersion": "77",
                        }
                    }
                )
            )
            result = self._run(root, "--archive", str(archive))

        self.assertEqual(1, result.returncode)
        self.assertEqual("artifact_version_mismatch", json.loads(result.stdout)["code"])

    def test_malformed_ipa_is_rejected_without_echoing_input_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="past-partner-ios-malformed-") as raw_root:
            root = Path(raw_root)
            self._create_project(root)
            ipa = root / "malformed.ipa"
            ipa.write_bytes(b"not an ipa")
            result = self._run(root, "--ipa", str(ipa))

        self.assertEqual(1, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("artifact_invalid", payload["code"])
        self.assertNotIn(str(ipa), result.stdout)

    def test_missing_artifacts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="past-partner-ios-missing-") as raw_root:
            root = Path(raw_root)
            self._create_project(root)
            result = self._run(root)

        self.assertEqual(1, result.returncode)
        self.assertEqual("artifact_missing", json.loads(result.stdout)["code"])

    def test_workflow_collects_only_redacted_json_after_each_build(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("collect_ios_release_evidence.py", workflow)
        self.assertIn("ios-release-evidence.json", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertIn("if: ${{ success() }}", workflow)
        self.assertIn("if-no-files-found: ignore", workflow)
        self.assertNotIn("upload-artifact", workflow.split("ios-release-evidence.json", 1)[0])
        self.assertNotIn("PAST_PARTNER_IOS_CERTIFICATE_BASE64", workflow.split("actions/upload-artifact@v4", 1)[0])

    def test_signed_workflow_invokes_root_signing_script_from_mobile_directory(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("run: bash ../scripts/validate_ios_signing.sh", workflow)
        self.assertIn("--output ios-release-evidence.json", workflow)


if __name__ == "__main__":
    unittest.main()
