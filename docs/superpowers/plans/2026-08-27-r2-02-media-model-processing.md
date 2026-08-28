# R2-02 Media Model Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add consent-gated, provider-neutral media analysis for uploaded image, audio, and video files, with bounded encrypted-payload handling, stable errors, and observable results for the Android client.

**Architecture:** Keep local `MediaInspector` metadata-only and introduce a separate media-analysis path. `MediaAnalysisService` will authorize an exact existing consent against provider/model capabilities before decrypting an import, copy the selected bounded payload to a controlled temporary file, invoke a `MediaAnalysisProviderAdapter` through `ProviderGateway`, and return only normalized descriptions/usage metadata. The first production transport will use the existing OpenAI-compatible HTTP boundary for image analysis; unsupported audio/video transports remain explicit `capability_not_supported` until an adapter advertises them.

**Tech Stack:** Python 3, existing ProviderGateway and JSON/multipart transports, AES-GCM UploadService streaming, SQLite metadata contracts, unittest, Flutter/Dart HTTP client and widget tests.

---

### Task 1: Add the media-analysis provider contract and gateway gate

**Files:**
- Modify: `src/providers/base.py`
- Modify: `src/providers/gateway.py`
- Modify: `tests/unit/test_provider_gateway.py`

- [x] **Step 1: Write the failing gateway tests**

Add a deterministic adapter with `analyze_media` and tests that prove:

```python
request = MediaAnalysisRequest(
    provider_id="test",
    model_id="deterministic-vision",
    media_type="image/png",
    media_path=Path("image.png"),
    prompt="描述图片",
)
result = gateway.analyze_media(request)
assert result.description == "测试媒体描述"
```

Also assert that an unknown model, a model/provider without the required `vision`/`audio`/`video` capability, a missing adapter, a mismatched adapter `provider_id`, and an adapter error all return stable `ProviderError` codes without falling back to chat.

- [x] **Step 2: Run the focused tests to verify the expected failure**

Run: `python -m unittest tests.unit.test_provider_gateway -v`

Expected: FAIL because `MediaAnalysisRequest`, `MediaAnalysisResult`, and `ProviderGateway.analyze_media` do not exist.

- [x] **Step 3: Implement the minimal provider contract**

Add immutable request/result dataclasses containing only bounded metadata and a controlled `Path`; add an optional `MediaAnalysisProviderAdapter` protocol with `supports_media(model_id, media_category)` and `analyze_media(request)`. Implement gateway capability checks using the existing catalog fields, adapter identity/callable validation, and `AdapterError` translation. Do not add media capability to a catalog model merely to make a test pass.

- [x] **Step 4: Run the focused provider tests**

Run: `python -m unittest tests.unit.test_provider_gateway tests.unit.test_native_provider_adapters -v`

Expected: PASS, with existing chat/fine-tuning tests unchanged.

- [x] **Step 5: Commit the provider contract slice**

```powershell
git add src/providers/base.py src/providers/gateway.py tests/unit/test_provider_gateway.py
git commit -m "feat: add capability-gated media provider contract"
```

### Task 2: Add bounded consent-aware media analysis service

**Files:**
- Create: `src/services/media_analysis_service.py`
- Modify: `src/server/application.py`
- Modify: `tests/unit/test_media_analysis_service.py`

- [x] **Step 1: Write failing service tests**

Cover exact consent authorization before any payload read, owner/import scope, completed-upload requirement, configured maximum media bytes, temporary-file cleanup on provider failure, redacted normalized result, and stable mappings for missing consent, revoked consent, storage failure, and provider failure.

- [x] **Step 2: Run the focused service tests and verify failure**

Run: `python -m unittest tests.unit.test_media_analysis_service -v`

Expected: FAIL because the service and application entry point do not exist.

- [x] **Step 3: Implement the service**

Reuse `UploadService.iter_payload(owner_id, import_id)` so encrypted chunks are decrypted in bounded blocks. Reject payloads larger than the configured per-analysis limit before provider handoff, materialize only inside the existing controlled temporary directory, call `MultimodalConsentGate.authorize`, and always delete the temporary file. Return provider/model/category, a bounded description, normalized usage, and an explicit `provider_transfer=true`; never return raw bytes, API keys, or local paths.

- [x] **Step 4: Run service and existing media tests**

Run: `python -m unittest tests.unit.test_media_analysis_service tests.unit.test_media_inspector tests.unit.test_upload_service -v`

Expected: PASS.

- [x] **Step 5: Commit the service slice**

```powershell
git add src/services/media_analysis_service.py src/server/application.py tests/unit/test_media_analysis_service.py
git commit -m "feat: add consent-aware bounded media analysis"
```

### Task 3: Implement the first real OpenAI-compatible image transport

**Files:**
- Modify: `src/providers/openai_compatible.py`
- Modify: `src/providers/transport.py`
- Modify: `tests/unit/test_provider_gateway.py`
- Modify: `tests/integration/test_provider_smoke.py`

- [x] **Step 1: Write failing transport tests**

Use an injected transport to assert an image request uses the selected model, a data URL derived from the controlled file, a bounded prompt, and no unrelated fields. Add malformed-response, timeout, rate-limit, and non-JSON cases with stable `AdapterError` codes. Include one local HTTP subprocess endpoint test to prove the request crosses the real transport boundary.

- [x] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.unit.test_provider_gateway tests.integration.test_provider_smoke -v`

Expected: FAIL because OpenAI-compatible adapters expose chat only.

- [x] **Step 3: Implement image analysis only where capability is advertised**

Encode the bounded file through a streaming-safe temporary-file reader, enforce the configured byte limit, send the provider's multimodal message shape, normalize the first text result and usage, and reject audio/video requests unless the adapter explicitly supports them. Do not silently reuse chat or infer capabilities from the file extension.

- [x] **Step 4: Run focused provider and smoke tests**

Run: `python -m unittest tests.unit.test_provider_gateway tests.integration.test_provider_smoke -v`

Expected: PASS.

- [x] **Step 5: Commit the image transport slice**

```powershell
git add src/providers/openai_compatible.py src/providers/transport.py tests/unit/test_provider_gateway.py tests/integration/test_provider_smoke.py
git commit -m "feat: add openai-compatible image analysis"
```

### Task 4: Expose the HTTP route and Android-observable state

**Files:**
- Modify: `src/server/http.py`
- Create: `tests/integration/test_http_media_analysis.py`
- Modify: `mobile/lib/core/api/past_partner_api.dart`
- Create: `mobile/lib/features/media/media_analysis_controller.dart`
- Create: `mobile/test/features/media/media_analysis_controller_test.dart`
- Modify: `docs/privacy_policy.md`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Write failing HTTP and Dart tests**

Assert the route requires owner authentication, rejects missing/revoked consent before payload transfer, returns a stable provider error, and exposes only normalized result fields. Dart tests must model loading, success, provider-unavailable, and capability-not-supported states without storing media bytes or bearer tokens.

- [ ] **Step 2: Run focused HTTP/Dart tests to verify failure**

Run: `python -m unittest tests.integration.test_http_media_analysis -v` and `flutter test test/features/media/media_analysis_controller_test.dart` from `mobile`.

Expected: FAIL because the route and controller do not exist.

- [ ] **Step 3: Implement the smallest route and client state machine**

Add `POST /api/v1/imports/{import_id}/media-analysis` with provider/model/consent/scope fields, route-template logging, stable status mapping, and no raw response echo. Add an Android controller that calls the route and exposes explicit retry/error states; the UI must require consent and show provider/model, media category, description, and usage without claiming OCR/ASR/video support when the provider does not advertise it.

- [ ] **Step 4: Run focused and mobile tests**

Run: `python -m unittest tests.integration.test_http_media_analysis tests.integration.test_http_api -v` and `flutter test` from `mobile`.

Expected: PASS; existing chat/import/consent routes remain unchanged.

- [ ] **Step 5: Update docs and commit the HTTP/client slice**

Document that local metadata inspection is provider-free, media analysis is consent-gated and capability-gated, unsupported categories fail closed, and provider retention/deletion boundaries remain explicit.

```powershell
git add src/server/http.py tests/integration/test_http_media_analysis.py mobile docs/privacy_policy.md docs/ROADMAP.md
git commit -m "feat: expose consent-gated media analysis"
```

### Task 5: Full verification and user acceptance handoff

**Files:**
- No production files beyond the previous tasks.

- [ ] **Step 1: Run repository verification**

Run `python -m compileall -q src tests`, `git diff --check`, focused Python/Dart suites, and the full `npm test`; external providers remain skipped unless disposable credentials are configured.

- [ ] **Step 2: Sync CodeGraph and inspect the final changed-file list**

Run `codegraph sync; codegraph status` with elevated permissions and confirm the index is up to date. Confirm no bearer, provider key, raw media, or full local path appears in tests, responses, or logs.

- [ ] **Step 3: Commit the verification evidence and hand off**

Keep `codex/r2-02-media-models` unmerged until user acceptance. After acceptance, merge to `main`, rerun full regression on `main`, push `origin/main`, and remove only this feature worktree/branch.

---

## Scope review

- Covered by Tasks 1-3: provider contract, capability gate, exact consent ordering, bounded encrypted payload handoff, stable errors, and one real image transport.
- Covered by Task 4: authenticated HTTP surface and Android-observable loading/success/failure states.
- Explicitly deferred: provider-specific native ASR/video endpoints, embeddings, OCR-specific structure extraction, third-party deletion, and iOS packaging. These remain R2-02 follow-up slices or later roadmap items and are not silently represented as complete by this plan.
