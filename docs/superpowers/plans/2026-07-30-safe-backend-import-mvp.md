# Safe Backend And Resumable Import MVP Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task, with `test-driven-development` for every behavior change and `verification-before-completion` before reporting success.

**Goal:** Deliver the first usable PC development loop on one safe Python backend: create a relationship persona, start and resume an import job up to 3 GiB, inspect provider/model choices, and chat only through an explicitly configured provider.

**Architecture:** Keep domain and service logic independent from HTTP so the same backend can serve the current Web client and future Flutter clients. A thin standard-library development server exposes versioned JSON APIs and streams chunk bodies directly to disk. Provider metadata and adapters live behind one gateway; deterministic responses exist only in test mode.

**Tech Stack:** Python 3.14 standard library, `unittest`, `ThreadingHTTPServer`, HTML/CSS/JavaScript, npm as an optional PC launcher, CodeGraph for structural checks, Playwright for browser acceptance.

---

## Scope Boundaries

This milestone implements the approved design's safe backend foundation plus the resumable upload protocol needed to remove the current small-upload blocker. It does not yet implement encrypted object storage, OCR/ASR, full WeChat/QQ proprietary database extraction, vector retrieval, provider fine-tuning jobs, authentication, billing, Flutter clients, or production deployment. Those remain subsequent milestones and the API contracts below must leave room for them.

## Task 1: Establish Domain Contracts And Safe Storage Paths

**Files:**

- Create: `src/domain/__init__.py`
- Create: `src/domain/personas.py`
- Create: `src/domain/messages.py`
- Create: `src/services/__init__.py`
- Create: `src/services/storage.py`
- Test: `tests/unit/test_personas.py`
- Test: `tests/unit/test_storage_paths.py`

**Step 1: Write failing tests**

- Require one of `father`, `mother`, `relative`, `friend`, `partner`, or `custom` when creating a persona.
- Require a non-empty custom label only for `custom`.
- Normalize imported messages to one schema with `sender_id`, `sender_name`, `content`, `timestamp`, `message_type`, and `attachments`.
- Reject absolute paths, `..`, separators, control characters, and identifiers that resolve outside the configured data root.

**Step 2: Run tests and confirm RED**

Run: `python -m unittest tests.unit.test_personas tests.unit.test_storage_paths -v`

Expected: imports fail because the new domain and storage modules do not exist.

**Step 3: Implement the minimum domain and path logic**

- Use enums/dataclasses for stable role and message contracts.
- Generate server-owned UUIDs for stored objects; never embed user identifiers or source filenames in storage paths.
- Resolve and compare paths against the configured root before creating or opening files.

**Step 4: Run tests and confirm GREEN**

Run: `python -m unittest tests.unit.test_personas tests.unit.test_storage_paths -v`

## Task 2: Add Persona Storage And Require Persona Before Import

**Files:**

- Create: `src/services/persona_service.py`
- Create: `src/services/import_service.py`
- Test: `tests/unit/test_persona_service.py`
- Test: `tests/unit/test_import_service.py`

**Step 1: Write failing tests**

- Persist persona metadata as UTF-8 JSON using atomic replacement.
- Reject import creation without an existing persona.
- Accept aggregate sizes from zero through `3 * 1024 ** 3` bytes and reject larger jobs.
- Store source display names only as metadata; storage filenames remain UUID-based.

**Step 2: Run tests and confirm RED**

Run: `python -m unittest tests.unit.test_persona_service tests.unit.test_import_service -v`

**Step 3: Implement persona and import services**

- Inject storage roots and clocks so tests never touch production data.
- Record import state as `created`, `uploading`, `uploaded`, `processing`, `completed`, or `failed`.
- Keep the 3 GiB limit configurable while defaulting to exactly 3,221,225,472 bytes.

**Step 4: Run tests and confirm GREEN**

Run: `python -m unittest tests.unit.test_persona_service tests.unit.test_import_service -v`

## Task 3: Implement Resumable, Integrity-Checked Chunk Uploads

**Files:**

- Modify: `src/services/import_service.py`
- Create: `src/services/upload_service.py`
- Test: `tests/unit/test_upload_service.py`
- Test: `tests/integration/test_large_upload_contract.py`

**Step 1: Write failing tests**

- Accept chunks in any order when each chunk has an index, declared length, and SHA-256 digest.
- Make an identical retry idempotent and reject a conflicting retry.
- Stream chunks to disk without buffering the complete import in memory.
- Reject a chunk or total committed size that crosses the job limit.
- Complete only when every byte is present and the optional whole-file digest matches.
- Simulate a 3 GiB job with sparse metadata and small boundary chunks instead of allocating 3 GiB in CI.

**Step 2: Run tests and confirm RED**

Run: `python -m unittest tests.unit.test_upload_service tests.integration.test_large_upload_contract -v`

**Step 3: Implement upload sessions**

- Write each chunk to a server-owned temporary part file with exclusive/atomic semantics.
- Persist a compact manifest after every accepted chunk.
- Assemble or expose the completed payload only after length and digest verification.

**Step 4: Run tests and confirm GREEN**

Run: `python -m unittest tests.unit.test_upload_service tests.integration.test_large_upload_contract -v`

## Task 4: Introduce The Provider Gateway And Catalog

**Files:**

- Create: `src/providers/__init__.py`
- Create: `src/providers/base.py`
- Create: `src/providers/catalog.py`
- Create: `src/providers/gateway.py`
- Create: `src/providers/openai_compatible.py`
- Create: `src/providers/testing.py`
- Test: `tests/unit/test_provider_catalog.py`
- Test: `tests/unit/test_provider_gateway.py`

**Step 1: Write failing tests**

- Catalog OpenAI, Anthropic, Gemini, DeepSeek, Xiaomi MiMo, Alibaba Qwen/DashScope, Ollama, custom OpenAI-compatible, and generic custom HTTP providers.
- Expose capability, context, modality, pricing-source, and credential-mode metadata without claiming live availability.
- Reject unknown providers/models with stable error codes.
- Refuse chat when no real provider is configured.
- Permit the deterministic test provider only when application mode is `test`.

**Step 2: Run tests and confirm RED**

Run: `python -m unittest tests.unit.test_provider_catalog tests.unit.test_provider_gateway -v`

**Step 3: Implement the gateway**

- Define one adapter protocol for chat and future embeddings/fine-tuning capabilities.
- Add provider presets as endpoint/capability metadata, not hard-coded credentials.
- Implement an injectable OpenAI-compatible HTTP transport for DeepSeek, MiMo, Qwen, Ollama, and custom compatible endpoints; tests use a fake transport and never call the network.

**Step 4: Run tests and confirm GREEN**

Run: `python -m unittest tests.unit.test_provider_catalog tests.unit.test_provider_gateway -v`

## Task 5: Expose One Versioned Python HTTP API

**Files:**

- Create: `src/server/__init__.py`
- Create: `src/server/config.py`
- Create: `src/server/application.py`
- Create: `src/server/http.py`
- Create: `src/server/__main__.py`
- Test: `tests/integration/test_http_api.py`
- Test: `tests/integration/test_static_security.py`

**Step 1: Write failing tests**

- Add `/api/v1/health`, `/api/v1/personas`, `/api/v1/imports`, chunk upload/status/complete routes, `/api/v1/providers`, `/api/v1/models`, and `/api/v1/chat`.
- Return structured JSON errors with stable codes and correct HTTP statuses.
- Require `Content-Length` for chunks and stream exactly that many bytes.
- Serve only allow-listed Web assets; traversal variants such as `/../package.json` must return 404.
- Support CORS preflight from explicitly configured local origins.

**Step 2: Run tests and confirm RED**

Run: `python -m unittest tests.integration.test_http_api tests.integration.test_static_security -v`

**Step 3: Implement the HTTP adapter**

- Route request parsing to application services; keep business rules out of the handler.
- Apply request-size limits separately for JSON bodies and streamed chunks.
- Redact exception details from client responses while preserving server logs.

**Step 4: Run tests and confirm GREEN**

Run: `python -m unittest tests.integration.test_http_api tests.integration.test_static_security -v`

## Task 6: Reconnect The Web Client To The Real Contracts

**Files:**

- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/styles.css`
- Test: `tests/integration/test_web_contract.py`

**Step 1: Write failing contract tests**

- Verify the page offers the six relationship choices and custom label behavior before file selection.
- Verify the client uses `/api/v1` on the current origin rather than a hard-coded second port.
- Verify user/model/import data is rendered with `textContent` or DOM nodes, not interpolated into `innerHTML`.
- Verify selected persona ID is included when creating an import job.

**Step 2: Run tests and confirm RED**

Run: `python -m unittest tests.integration.test_web_contract -v`

**Step 3: Implement the client flow**

- Create persona, choose files, create import job, upload chunks with progress/retry, complete the job, and show provider/model choices.
- Keep layout compatible with desktop and narrow mobile widths without treating the Web UI as the future Flutter implementation.

**Step 4: Run tests and confirm GREEN**

Run: `python -m unittest tests.integration.test_web_contract -v`

## Task 7: Remove Fake Success Paths And Unify Launch Modes

**Files:**

- Modify: `src/training/fine_tuner.py`
- Modify: `web/server.py`
- Modify: `web/server_advanced.py`
- Modify: `web/server.js`
- Modify: `package.json`
- Create: `scripts/run_server.ps1`
- Modify: `README.md`
- Modify: `docs/chat_import_guide.md`
- Test: `tests/unit/test_fine_tuner_contract.py`
- Test: `tests/integration/test_launch_contract.py`

**Step 1: Write failing tests**

- Fine-tuning without a configured capable provider must report `capability_not_configured`, never fabricated metrics or success.
- `python -m src.server`, PowerShell, and npm must all delegate to the same Python application.
- The documented default port and environment variables must match runtime behavior.

**Step 2: Run tests and confirm RED**

Run: `python -m unittest tests.unit.test_fine_tuner_contract tests.integration.test_launch_contract -v`

**Step 3: Implement launch compatibility and truthful failures**

- Keep npm as a convenience wrapper only.
- Turn legacy servers into clear delegating shims or deprecation messages; do not leave multiple competing APIs.
- Update user-facing docs from the obsolete 50 MB/direct-upload description to resumable 3 GiB import sessions.

**Step 4: Run tests and confirm GREEN**

Run: `python -m unittest tests.unit.test_fine_tuner_contract tests.integration.test_launch_contract -v`

## Task 8: Full Verification And Browser Acceptance

**Files:**

- Modify as needed only for defects exposed by verification.

**Step 1: Run the complete Python suite**

Run: `python -m unittest discover -s tests -p "test*.py" -v`

Expected: all tests pass without network access or provider credentials.

**Step 2: Run syntax and package checks**

Run: `python -m compileall -q src tests utils models web`

Run: `npm test`

Expected: both exit successfully.

**Step 3: Run the development server and smoke the API**

Run: `python -m src.server --host 127.0.0.1 --port 8080 --data-dir data/runtime`

- Confirm health, persona creation, import creation, a chunk retry, completion, provider catalog, and a deliberate unconfigured-provider chat failure.
- Confirm `/../package.json` cannot be read.

**Step 4: Run Playwright acceptance on desktop and mobile viewports**

- Verify identity selection precedes import.
- Upload a generated multi-chunk fixture, interrupt/retry one chunk, and complete it.
- Verify provider/model choices render safely and the page has no console errors or overlapping controls.

**Step 5: Refresh CodeGraph and inspect impact**

Run: `codegraph sync`

- Confirm the index is current.
- Inspect callers/impact for the legacy upload and fine-tuning entry points before finalizing compatibility behavior.

**Step 6: Commit the verified milestone**

Run: `git status --short`

Run: `git diff --check`

Commit only source, tests, and documentation; keep runtime imports, credentials, generated models, and CodeGraph state ignored.

