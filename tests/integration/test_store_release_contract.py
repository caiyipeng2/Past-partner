from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
MOBILE = ROOT / "mobile"


class StoreReleaseContractTests(unittest.TestCase):
    def test_android_store_release_requires_explicit_environment_signing(self) -> None:
        gradle = (MOBILE / "android" / "app" / "build.gradle.kts").read_text()
        for name in (
            "PAST_PARTNER_ANDROID_STORE_RELEASE",
            "PAST_PARTNER_ANDROID_KEYSTORE_FILE",
            "PAST_PARTNER_ANDROID_KEYSTORE_PASSWORD",
            "PAST_PARTNER_ANDROID_KEY_ALIAS",
            "PAST_PARTNER_ANDROID_KEY_PASSWORD",
        ):
            self.assertIn(name, gradle)
        self.assertIn("signingConfigs.create(\"release\")", gradle)
        self.assertIn("GradleException", gradle)
        self.assertNotIn("storePassword = \"", gradle)
        self.assertNotIn("keyPassword = \"", gradle)

    def test_build_helper_exposes_store_release_mode_without_secret_literals(self) -> None:
        script = (ROOT / "scripts" / "build_mobile_apk.ps1").read_text()
        self.assertRegex(script, r"\[switch\]\$StoreRelease")
        self.assertIn("Assert-StoreReleaseEnvironment", script)
        self.assertIn("PAST_PARTNER_ANDROID_STORE_RELEASE", script)
        self.assertNotRegex(script, r"(?i)(store|key)Password\s*=\s*['\"]")

    def test_android_and_ios_release_versions_match_pubspec(self) -> None:
        pubspec = (MOBILE / "pubspec.yaml").read_text()
        version = re.search(r"(?m)^version:\s*([^\s]+)", pubspec).group(1)
        build_name, build_code = version.split("+", 1)
        plist = (MOBILE / "ios" / "Runner" / "Info.plist").read_text()
        self.assertIn(f"<key>CFBundleShortVersionString</key><string>{build_name}</string>", plist)
        self.assertIn(f"<key>CFBundleVersion</key><string>{build_code}</string>", plist)

    def test_release_transport_is_fail_closed_and_debug_only_cleartext(self) -> None:
        release_manifest = (MOBILE / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text()
        debug_manifest = (MOBILE / "android" / "app" / "src" / "debug" / "AndroidManifest.xml").read_text()
        plist = (MOBILE / "ios" / "Runner" / "Info.plist").read_text()
        self.assertIn('android:usesCleartextTraffic="false"', release_manifest)
        self.assertNotIn('android:usesCleartextTraffic="true"', release_manifest)
        self.assertIn('android:usesCleartextTraffic="true"', debug_manifest)
        self.assertNotIn("NSAllowsArbitraryLoads", plist)
        self.assertNotIn("NSExceptionDomains", plist)


if __name__ == "__main__":
    unittest.main()
