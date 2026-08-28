from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_ios_signing.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "ios-release.yml"


class IosSigningContractTests(unittest.TestCase):
    def test_signing_script_is_macos_only_and_fail_closed(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("set -euo pipefail", script)
        self.assertIn('$(uname -s)" != "Darwin"', script)
        self.assertIn("ios_signing_requires_macos", script)
        for name in (
            "PAST_PARTNER_IOS_CERTIFICATE_BASE64",
            "PAST_PARTNER_IOS_CERTIFICATE_PASSWORD",
            "PAST_PARTNER_IOS_PROVISIONING_PROFILE_BASE64",
            "PAST_PARTNER_IOS_KEYCHAIN_PASSWORD",
            "PAST_PARTNER_IOS_TEAM_ID",
            "PAST_PARTNER_IOS_BUNDLE_ID",
            "PAST_PARTNER_IOS_EXPORT_OPTIONS_BASE64",
        ):
            self.assertIn(name, script)
        self.assertIn("security create-keychain", script)
        self.assertIn("security delete-keychain", script)
        self.assertIn("original_keychains", script)
        self.assertIn("existing_profile_backup", script)
        self.assertIn("TeamIdentifier", script)
        self.assertIn("application-identifier", script)
        self.assertIn("CFBundleIdentifier", script)
        self.assertIn("ios_profile_identity_mismatch", script)
        self.assertIn("ios_export_identity_mismatch", script)
        self.assertIn("flutter build ipa --release --export-options-plist", script)
        self.assertIn("trap cleanup EXIT", script)
        self.assertNotIn("BEGIN PRIVATE KEY", script)
        self.assertNotIn("certificate-password", script)

    def test_workflow_has_explicit_secret_gated_store_job(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("store_release:", workflow)
        self.assertIn("type: boolean", workflow)
        self.assertIn("ios-store-release:", workflow)
        self.assertIn("if: ${{ inputs.store_release == true }}", workflow)
        self.assertIn("bash scripts/validate_ios_signing.sh", workflow)
        self.assertIn("working-directory: mobile", workflow)
        for name in (
            "PAST_PARTNER_IOS_CERTIFICATE_BASE64",
            "PAST_PARTNER_IOS_CERTIFICATE_PASSWORD",
            "PAST_PARTNER_IOS_PROVISIONING_PROFILE_BASE64",
            "PAST_PARTNER_IOS_KEYCHAIN_PASSWORD",
            "PAST_PARTNER_IOS_TEAM_ID",
            "PAST_PARTNER_IOS_BUNDLE_ID",
            "PAST_PARTNER_IOS_EXPORT_OPTIONS_BASE64",
        ):
            self.assertIn(f"secrets.{name}", workflow)
        self.assertNotIn("BEGIN PRIVATE KEY", workflow)
        self.assertNotIn("certificate-password", workflow)

    def test_store_job_runs_after_source_policy_and_never_uses_plaintext_values(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertLess(
            workflow.index("python scripts/check_ios_release.py"),
            workflow.index("ios-store-release:"),
        )
        self.assertIn("environment: ios-release", workflow)
        self.assertNotIn("echo $PAST_PARTNER_IOS_", workflow)


if __name__ == "__main__":
    unittest.main()
