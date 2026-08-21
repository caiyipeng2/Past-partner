# Past-partner mobile client

This Flutter client is the Android-first mobile surface for the companion
workspace. The Windows development workflow keeps the Python service as the
runtime entry point and uses Flutter for client tests and APK builds.

## Local APK validation

The existing command builds both debug and release APKs with the local debug
signing fallback used for PC/device acceptance:

```powershell
.\scripts\build_mobile_apk.ps1 -OutputDirectory E:\Tools
```

The output directory keeps only the latest timestamped
`Past-partner_<version>_<yyyyMMdd_HHmm>_<debug|release>.apk` files.

## Store-release APK

Store mode is explicit and fail-closed. Set these process environment variables
to a real keystore kept outside Git:

```powershell
$env:PAST_PARTNER_ANDROID_KEYSTORE_FILE = 'E:\secrets\past-partner-release.jks'
$env:PAST_PARTNER_ANDROID_KEYSTORE_PASSWORD = '<secret>'
$env:PAST_PARTNER_ANDROID_KEY_ALIAS = '<alias>'
$env:PAST_PARTNER_ANDROID_KEY_PASSWORD = '<secret>'
.\scripts\build_mobile_apk.ps1 -StoreRelease -OutputDirectory E:\Tools
```

`-StoreRelease` builds only the release variant, requires all four values and a
regular keystore file, selects the dedicated Gradle release signing config,
and removes stale `Past-partner_*.apk` files before writing the new artifact.
Secrets are never written to the repository, APK metadata, or build logs.

## iOS scope

Windows CI runs code-level iOS checks for version alignment and ATS transport
policy. Xcode archives, App Store signing, notarization, and real iOS device
validation are intentionally outside this task.
