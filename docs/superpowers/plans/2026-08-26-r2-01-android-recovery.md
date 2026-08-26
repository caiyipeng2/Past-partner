# R2-01 Android Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 让 Android 客户端在进程重启和网络波动后保留用户选择、恢复上传任务，并为后台任务提供可观察的通知状态。

**Architecture:** 先把 R2-01 拆成三个可独立验收的移动端切片：模型选择持久化、会话/上传恢复状态、Android 后台调度与通知。模型选择只保存 owner 范围的 provider/model 标识，不保存 API Key；上传恢复继续复用服务端缺片接口和现有安全恢复清单，Android 原生后台调度只负责唤醒 Dart 上传编排，不复制业务协议。

**Tech Stack:** Flutter/Dart, `shared_preferences`, `flutter_secure_storage`, Android WorkManager/notification bridge, existing versioned HTTP API.

---

### Task 1: Model Selection Persistence

**Files:**
- Create: `mobile/lib/features/models/model_selection_store.dart`
- Modify: `mobile/lib/features/models/model_controller.dart`
- Modify: `mobile/lib/features/persona/persona_workspace_screen.dart`
- Modify: `mobile/lib/app/past_partner_app.dart`
- Test: `mobile/test/features/models/model_controller_test.dart`
- Test: `mobile/test/features/models/model_selection_store_test.dart`

- [ ] Write failing tests for owner-scoped read/write, malformed-value rejection, and controller restoration only when the refreshed catalog still contains the stored model.
- [ ] Run the focused tests and confirm they fail because the store/controller integration is absent.
- [ ] Implement the bounded `ModelSelection` value object, in-memory and SharedPreferences stores, and controller restoration/persistence with non-secret provider/model IDs.
- [ ] Run focused tests, `flutter analyze`, and the existing model/persona widget tests.
- [ ] Commit as `feat(mobile): persist selected model per owner` and wait for user acceptance.

### Task 2: Session And Upload Recovery State

**Files:**
- Modify: `mobile/lib/core/session/session_controller.dart`
- Modify: `mobile/lib/features/imports/import_resume.dart`
- Modify: `mobile/lib/features/imports/import_upload_controller.dart`
- Modify: `mobile/lib/features/imports/import_workspace_screen.dart`
- Test: `mobile/test/core/session/session_controller_test.dart`
- Test: `mobile/test/features/imports/import_upload_controller_test.dart`

- [ ] Add tests for expired-session refresh, resumable-file availability checks, and idempotent recovery after a process restart.
- [ ] Implement only the bounded state transitions and stable user-facing errors; never persist access tokens in upload manifests.
- [ ] Run focused tests and a full Flutter test/analyze pass.
- [ ] Commit as `feat(mobile): harden session and upload recovery` and wait for user acceptance.

### Task 3: Android Background Upload And Notifications

**Files:**
- Modify: `mobile/pubspec.yaml`
- Create: `mobile/lib/features/imports/background_upload.dart`
- Modify: `mobile/lib/features/imports/import_upload_controller.dart`
- Modify: `mobile/android/app/src/main/AndroidManifest.xml`
- Create/Modify: Android native WorkManager bridge files required by the selected plugin.
- Test: Dart unit/widget tests plus Android manifest/compile checks.

- [ ] Add a failing scheduler contract test covering enqueue, progress notification, retryable failure, cancellation, and completion cleanup.
- [ ] Integrate an Android-only worker with bounded retries and the existing upload controller; iOS receives a no-op adapter until its native background path is planned.
- [ ] Verify Android debug/release builds and a connected-device process-kill recovery smoke; document OS background limits and notification permission behavior.
- [ ] Commit as `feat(android): add resumable background upload worker` and wait for user acceptance.

### Task 4: R2-01 Integration Verification

**Files:**
- Modify: `docs/ROADMAP.md`
- Modify: `DEVELOPMENT.md`
- Test: all mobile tests, Android build, and device smoke evidence.

- [ ] Record the three accepted sub-slices and explicit limits (iOS static only, OS scheduling is best effort, no provider key on device).
- [ ] Run `flutter test`, `flutter analyze`, Android debug/release build, and the connected-device recovery path.
- [ ] Merge only after user acceptance, rerun the full repository regression, refresh CodeGraph, and push `origin/main`.

---

**Spec coverage review:** Task 1 covers model selection persistence; Task 2 covers real session/upload recovery; Task 3 covers Android background scheduling and notifications; Task 4 covers the requested verification and documentation boundary. No task claims iOS native packaging or guaranteed Android execution after force-stop.
