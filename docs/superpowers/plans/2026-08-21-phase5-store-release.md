# Phase 5 Store Release Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the original Phase 5 store-release preparation item with an Android-first, fail-closed signed-release path and static iOS release checks, without publishing to either store or requiring an iOS machine build.

**Architecture:** Keep ordinary developer APK behavior unchanged, including the existing debug-signed release artifact used for local acceptance. Add an explicit `-StoreRelease` path that requires a complete keystore environment and makes Gradle select the release signing config; a missing or partial configuration fails before the release build. Keep credentials in environment variables only. Add repository-level contract tests for Android/iOS transport policy, version alignment, signing gates, and artifact naming/cleanup.

**Tech Stack:** Flutter/Dart, Android Gradle Kotlin DSL, PowerShell, Python `unittest`, existing APK build helper.

---

### Task 1: Define release-readiness contracts

**Files:**
- Modify: `scripts/build_mobile_apk_test.ps1`
- Create: `tests/integration/test_store_release_contract.py`
- Create: `mobile/test/store_release_contract_test.dart`

- [x] **Step 1: Add failing PowerShell assertions for store-release mode.** Extend the script test to require an `Assert-StoreReleaseEnvironment` helper, verify a complete temporary keystore environment is accepted, verify a missing alias/password is rejected without echoing secret values, and verify `Get-MobileApkName` still produces the approved `Past-partner_<version>_<yyyyMMdd_HHmm>_release.apk` form.
- [x] **Step 2: Run `powershell -ExecutionPolicy Bypass -File scripts/build_mobile_apk_test.ps1` and verify the new assertion fails because the helper is not present.**
- [x] **Step 3: Add failing Python contracts.** Assert the Android release Gradle file reads all four signing variables and a store-release flag, the shared manifest forbids cleartext while only the debug manifest permits loopback cleartext, the iOS plist has no ATS exception, Android/iOS versions match `mobile/pubspec.yaml`, and the build script exposes `-StoreRelease` without hardcoded secrets.
- [x] **Step 4: Run `python -m unittest tests.integration.test_store_release_contract -v` and verify it fails on the missing signing/build contract.**
- [x] **Step 5: Add the Dart release contract test.** Assert the same source-level transport/version invariants from the mobile package so `flutter test` protects the client release boundary independently of Python tests.

### Task 2: Implement explicit Android Store Release signing

**Files:**
- Modify: `mobile/android/app/build.gradle.kts`
- Modify: `scripts/build_mobile_apk.ps1`
- Modify: `scripts/build_mobile_apk_test.ps1`

- [x] **Step 1: Add `Assert-StoreReleaseEnvironment` in the PowerShell helper.** Require `PAST_PARTNER_ANDROID_KEYSTORE_FILE`, `PAST_PARTNER_ANDROID_KEYSTORE_PASSWORD`, `PAST_PARTNER_ANDROID_KEY_ALIAS`, and `PAST_PARTNER_ANDROID_KEY_PASSWORD`; require the keystore path to be a regular file; reject whitespace-only values; and throw only a stable message containing variable names, never values or paths.
- [x] **Step 2: Add the `-StoreRelease` switch without changing the default path.** In store-release mode validate the environment, set the child-process-only `PAST_PARTNER_ANDROID_STORE_RELEASE=true`, build only `flutter build apk --release`, copy only the release artifact using the existing naming rule, remove stale named APKs first, and restore the parent environment variable in `finally`.
- [x] **Step 3: Update `build.gradle.kts` to read signing values only from Gradle providers.** When the store-release flag is true, fail with `GradleException` if any variable is missing or the file is absent; otherwise use a dedicated release signing config. Preserve the existing debug signing fallback only when the explicit store-release flag is false, so current local debug/release acceptance remains compatible.
- [x] **Step 4: Run the PowerShell script test and Python signing contracts until green.** Confirm no secret value appears in failure output.

### Task 3: Add version and transport release checks

**Files:**
- Modify: `mobile/test/release_transport_contract_test.dart`
- Modify: `mobile/test/store_release_contract_test.dart`
- Create: `tests/integration/test_store_release_contract.py`
- Modify: `mobile/ios/Runner/Info.plist`
- Modify: `mobile/android/app/src/main/AndroidManifest.xml` only if the contract exposes a real gap

- [x] **Step 1: Assert the pubspec version is the single source of truth.** Verify Android uses Flutter-injected `versionCode`/`versionName` and iOS `CFBundleShortVersionString`/`CFBundleVersion` match `0.1.0+1` at the current baseline; future version bumps must update both files in one change.
- [x] **Step 2: Assert release transport policy.** Check Android merged-manifest input has `usesCleartextTraffic=false`, no release `networkSecurityConfig` permits cleartext, debug cleartext is isolated to `src/debug`, and iOS contains no `NSAllowsArbitraryLoads` or exception domains. Do not add runtime HTTP exceptions for release.
- [x] **Step 3: Keep iOS verification code-only.** Do not invoke Xcode or claim a real iOS archive; the test must be runnable on Windows and report the static policy only.

### Task 4: Document the release boundary

**Files:**
- Modify: `README.md`
- Modify: `mobile/README.md`
- Modify: `docs/privacy_policy.md`
- Modify: `.env.example`

- [x] **Step 1: Document the explicit Android command.** Show `scripts/build_mobile_apk.ps1 -StoreRelease -OutputDirectory E:\Tools`, list the four environment variables by name, state that credentials and keystore files stay outside Git, and preserve the existing local command for debug/release acceptance.
- [x] **Step 2: Document artifact handling.** State that store mode emits only the timestamped release APK, removes old `Past-partner_*.apk` outputs, and never commits generated APKs.
- [x] **Step 3: State iOS scope honestly.** Document static ATS/version checks only; App Store signing, archive, notarization, and device validation remain outside the Windows task.

### Task 5: Verify and hand off

**Files:**
- Modify: files above only.

- [x] **Step 1: Run `powershell -ExecutionPolicy Bypass -File scripts/build_mobile_apk_test.ps1` and the focused Python contract test.**
- [x] **Step 2: Run `flutter test` and `flutter analyze` through an ASCII drive mapping if the non-ASCII worktree path reproduces the known analyzer LSP framing failure.**
- [x] **Step 3: Run Android `flutter build apk --debug` and `flutter build apk --release` for the normal developer path; run the store-release path only with a disposable test keystore and never commit artifacts.
- [x] **Step 4: Run `python -m unittest discover -s tests -p "test*.py" -v`, `npm test`, `python -m compileall -q src tests`, `git diff --check`, and `codegraph sync; codegraph status`.
- [x] **Step 5: Commit only this task on `feature/phase5-store-release`, report exact verification and explicit iOS/store exclusions, and wait for user acceptance before merge/push.

## Acceptance

This Phase 5 slice is ready when normal developer APK builds remain compatible, `-StoreRelease` rejects missing/partial signing configuration without leaking secrets, a complete configuration selects non-debug release signing, artifacts follow the approved name and cleanup rule, Android/iOS release transport and version contracts pass, Flutter/Python/full regressions pass, and no store publication or iOS archive is claimed.
