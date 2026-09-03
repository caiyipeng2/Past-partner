# R2-02 Video Semantic Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separately gated video semantic-analysis slice that reuses the existing owner, consent, temporary-file, and normalized media-result boundaries without claiming universal provider support.

**Architecture:** Keep `MediaAnalysisService` and `POST /api/v1/imports/{import_id}/media-analysis` as the only ownership and consent boundary. Add explicit per-model video capability configuration and a provider adapter operation whose endpoint shape is configured and validated instead of assuming every OpenAI-compatible service accepts video. The first implementation will use an injected/local HTTP transport for deterministic verification; production video support is enabled only for providers whose configured endpoint and model contract pass the same capability checks.

**Tech Stack:** Python 3, existing `ProviderGateway`, `ProviderCatalog`, AES-GCM `UploadService`, bounded multipart transport, SQLite metadata contracts, unittest, local HTTP smoke, existing Flutter media-analysis state.

---

### Task 1: Explicit video capability and endpoint configuration

**Files:**
- Modify: `src/providers/catalog.py`
- Modify: `src/providers/configuration.py`
- Modify: `src/providers/openai_compatible.py`
- Modify: `src/server/application.py`
- Test: `tests/unit/test_provider_catalog.py`
- Test: `tests/unit/test_provider_configuration.py`

- [x] **Step 1: Write failing tests**

Cover `PAST_PARTNER_<PROVIDER>_VIDEO_MODELS` as a subset of the provider model allowlist, a required configured video endpoint path, model-level `video` capability without leaking to other models, and rejection when the endpoint path is missing or unsafe. Verify default catalog models remain video-disabled.

- [x] **Step 2: Run the focused tests and verify the expected failure**

```powershell
python -m unittest tests.unit.test_provider_catalog tests.unit.test_provider_configuration -v
```

Expected: FAIL because only image/audio capability configuration exists.

- [x] **Step 3: Implement minimal configuration wiring**

Add a validated per-provider video model set and endpoint path to the compatible adapter configuration. Extend `ProviderCatalog.with_configured` so explicit runtime/static models receive only `video` and the provider receives `video` only when at least one configured model is enabled. Do not add video to default catalog metadata or infer capability from file extension.

- [x] **Step 4: Run catalog/config/gateway regressions**

```powershell
python -m unittest tests.unit.test_provider_catalog tests.unit.test_provider_configuration tests.unit.test_provider_gateway -v
```

Expected: PASS with image, audio, chat, and fine-tuning behavior unchanged.

- [x] **Step 5: Commit the capability slice**

```powershell
git add src/providers/catalog.py src/providers/configuration.py src/providers/openai_compatible.py src/server/application.py tests/unit/test_provider_catalog.py tests/unit/test_provider_configuration.py
git commit -m "feat: configure explicit video provider capabilities"
```

### Task 2: Provider-neutral bounded video request

**Files:**
- Modify: `src/providers/base.py`
- Modify: `src/providers/openai_compatible.py`
- Modify: `src/providers/transport.py`
- Test: `tests/unit/test_provider_gateway.py`
- Test: `tests/unit/test_provider_transport.py`

- [x] **Step 1: Write failing adapter tests**

Assert an enabled video model sends only the controlled temporary file, model, bounded prompt, and explicitly configured endpoint fields; the file part has a safe generated video filename and validated MIME. Add malformed response, unsupported model/category, missing file, oversize file, timeout, rate-limit, non-JSON, and protocol-interruption cases with stable errors. Ensure image and audio paths do not change.

- [x] **Step 2: Run tests to verify the expected failure**

```powershell
python -m unittest tests.unit.test_provider_gateway tests.unit.test_provider_transport -v
```

Expected: FAIL because the compatible adapter has no video operation.

- [x] **Step 3: Implement the minimal video operation**

Add a provider-neutral video request/response contract and a bounded transport invocation. Validate `video/*`, explicit capability, configured endpoint path, file size, and non-empty normalized description. Map provider failures to stable codes and never return bytes, local paths, provider keys, or unbounded provider response fields.

- [x] **Step 4: Run provider regressions**

```powershell
python -m unittest tests.unit.test_provider_gateway tests.unit.test_provider_transport tests.unit.test_provider_catalog tests.unit.test_provider_configuration -v
```

Expected: PASS.

- [x] **Step 5: Commit the provider operation**

```powershell
git add src/providers/base.py src/providers/openai_compatible.py src/providers/transport.py tests/unit/test_provider_gateway.py tests/unit/test_provider_transport.py
git commit -m "feat: add bounded video semantic provider contract"
```

### Task 3: Real local HTTP smoke and existing service boundary

**Files:**
- Modify: `tests/integration/test_provider_smoke.py`
- Modify: `tests/unit/test_media_analysis_service.py`
- Modify: `tests/integration/test_http_media_analysis.py`

- [x] **Step 1: Write failing local HTTP/boundary tests**

Extend the local HTTP fixture to parse the configured video multipart shape and return a bounded normalized description. Verify path, safe filename, MIME, model, prompt, file bytes, authorization, exact video consent, owner/import scope, temporary plaintext cleanup, normalized `media_category=video`, and redacted HTTP response.

- [x] **Step 2: Run tests and verify expected failure**

```powershell
python -m unittest tests.integration.test_provider_smoke tests.unit.test_media_analysis_service tests.integration.test_http_media_analysis -v
```

Expected: FAIL before the video adapter and capability wiring are complete.

- [x] **Step 3: Keep the existing service and route as the sole boundary**

Do not add a second video route, bypass consent, or upload the full multi-file import. Reuse one selected file, bounded temporary storage, exact `video` consent, and the existing normalized `description` result shape.

- [x] **Step 4: Run the integration suite**

```powershell
python -m unittest tests.integration.test_provider_smoke tests.unit.test_media_analysis_service tests.integration.test_http_media_analysis tests.unit.test_provider_gateway -v
```

Expected: PASS; unsupported or unconfigured video models fail closed.

- [x] **Step 5: Commit integration coverage**

```powershell
git add tests/integration/test_provider_smoke.py tests/unit/test_media_analysis_service.py tests/integration/test_http_media_analysis.py
git commit -m "test: verify consent-gated video semantic analysis"
```

### Task 4: Documentation and final verification

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/privacy_policy.md`

- [x] **Step 1: Document the opt-in video boundary**

Document `PAST_PARTNER_<PROVIDER>_VIDEO_MODELS`, the provider-specific endpoint requirement, exact video consent, bounded temporary-file handling, normalized description response, and the fact that OCR, streaming video, native provider variants, embeddings, and third-party deletion remain deferred.

- [x] **Step 2: Run verification**

Run `npm test`, `flutter test`, `dart analyze`, `python -m compileall -q src tests scripts`, and `git diff --check`. External provider tests remain opt-in and must not use real user media without explicit authorization.

- [x] **Step 3: Sync CodeGraph and inspect sensitive boundaries**

Run `codegraph sync; codegraph status` with elevated permissions. Confirm no bearer, API key, full path, raw video, or provider response is returned or logged.

- [x] **Step 4: Commit and hand off**

Keep `codex/r2-02-video-analysis` unmerged until user acceptance. After acceptance, merge `main`, rerun the full regression, push `origin/main`, and remove only this feature worktree/branch.

---

## Scope review

- Covered: explicit video capability, provider-specific endpoint configuration, bounded video request transport, exact consent/owner boundaries, stable errors, local HTTP smoke, and normalized Android-observable result reuse.
- Deferred: OCR, streaming video, provider-native endpoint variants not matching the configured contract, embeddings, automatic third-party deletion, and iOS packaging.
