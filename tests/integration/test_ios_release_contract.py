from __future__ import annotations

import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_ios_release.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ios-release.yml"


class IosReleaseContractTests(unittest.TestCase):
    def test_static_checker_reports_aligned_release_metadata(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--project-root", str(ROOT)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("ok", payload["status"])
        self.assertEqual("0.1.0", payload["version"]["name"])
        self.assertEqual("1", payload["version"]["code"])
        self.assertEqual({"ats_exceptions": False, "version_alignment": True}, payload["checks"])

    def test_workflow_is_manual_macos_no_codesign_and_secret_free(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("runs-on: macos-latest", workflow)
        self.assertIn("flutter pub get", workflow)
        self.assertIn("flutter analyze", workflow)
        self.assertIn("flutter test", workflow)
        self.assertIn("flutter build ipa --release --no-codesign", workflow)
        self.assertNotIn("APPLE_CERTIFICATE_PASSWORD:", workflow)
        self.assertNotIn("APP_STORE_CONNECT_API_KEY:", workflow)
        self.assertNotIn("BEGIN PRIVATE KEY", workflow)

    def test_workflow_runs_static_policy_before_build(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertLess(
            workflow.index("python scripts/check_ios_release.py"),
            workflow.index("flutter build ipa --release --no-codesign"),
        )
        self.assertIn("if: ${{ runner.os == 'macOS' }}", workflow)

    def test_static_checker_rejects_ats_exception_in_a_fixture_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="past-partner-ios-policy-") as raw_root:
            root = Path(raw_root)
            (root / "mobile").mkdir()
            (root / "mobile" / "ios" / "Runner").mkdir(parents=True)
            (root / "mobile" / "pubspec.yaml").write_text("name: past_partner\nversion: 0.1.0+1\n", encoding="utf-8")
            (root / "mobile" / "ios" / "Runner" / "Info.plist").write_bytes(
                plistlib.dumps(
                    {
                        "CFBundleShortVersionString": "0.1.0",
                        "CFBundleVersion": "1",
                        "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
                    }
                )
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--project-root", str(root)],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )

        self.assertEqual(1, result.returncode)
        self.assertEqual("ats_exception_present", json.loads(result.stdout)["code"])

    def test_static_checker_rejects_version_mismatch_in_a_fixture_project(self) -> None:
        with tempfile.TemporaryDirectory(prefix="past-partner-ios-version-") as raw_root:
            root = Path(raw_root)
            (root / "mobile").mkdir()
            (root / "mobile" / "ios" / "Runner").mkdir(parents=True)
            (root / "mobile" / "pubspec.yaml").write_text("name: past_partner\nversion: 0.1.0+2\n", encoding="utf-8")
            (root / "mobile" / "ios" / "Runner" / "Info.plist").write_bytes(
                plistlib.dumps(
                    {
                        "CFBundleShortVersionString": "0.1.0",
                        "CFBundleVersion": "1",
                    }
                )
            )
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--project-root", str(root)],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )

        self.assertEqual(1, result.returncode)
        self.assertEqual("version_mismatch", json.loads(result.stdout)["code"])


if __name__ == "__main__":
    unittest.main()
