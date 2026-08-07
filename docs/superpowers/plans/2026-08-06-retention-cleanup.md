# P0-27 Retention Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded, configurable cleanup policy for owner-scoped imports that have explicitly reached `failed` or `cancelled` terminal states, including their encrypted chunks, payloads, manifests, and metadata.

**Architecture:** Keep the existing HTTP/Application/Service/Repository layering. `RetentionService` will select only terminal jobs older than the configured age, while `UploadService` remains the single owner-checked deletion path for filesystem and encrypted metadata cleanup. The startup path will run one bounded cleanup pass after storage services are assembled; `uploaded`, `processing`, and `completed` data will never be removed by this task because this codebase does not yet record successful normalization.

**Tech Stack:** Python standard library, dataclass configuration, encrypted SQLite repositories, AES-GCM object storage, `unittest`, npm test wrapper, CodeGraph.

---

### Task 1: Establish branch, plan, and baseline

**Files:**
- Create: `docs/superpowers/plans/2026-08-06-retention-cleanup.md`

- [ ] **Step 1: Create `feature/p0-27-raw-retention` from clean `main`.**
- [ ] **Step 2: Run `npm test` before production edits; if the existing P0-24 timestamp-tie ordering test reproduces, record and fix that prerequisite before continuing.**
- [ ] **Step 3: Confirm `git status --short` is empty and CodeGraph is current.**

### Task 2: Add red tests for retention selection and configuration

**Files:**
- Modify: `tests/unit/test_server_config.py`
- Modify: `tests/unit/test_import_repository.py`
- Create: `tests/unit/test_retention_service.py`

- [ ] **Step 1: Test `PAST_PARTNER_RAW_RETENTION_SECONDS` parsing and reject negative values.**
- [ ] **Step 2: Test repository selection returns only owner-matching `failed`/`cancelled` jobs older than a supplied UTC cutoff.**
- [ ] **Step 3: Test a zero retention value disables cleanup selection.**
- [ ] **Step 4: Run the focused tests and verify they fail because the configuration field, repository selector, and service do not exist.**

### Task 3: Implement bounded terminal-state cleanup

**Files:**
- Modify: `src/server/config.py`
- Modify: `src/services/import_repository.py`
- Modify: `src/services/import_service.py`
- Create: `src/services/retention_service.py`
- Modify: `src/server/application.py`

- [ ] **Step 1: Add `raw_retention_seconds` with default `0` and environment parsing; `0` disables cleanup, while positive values are limited to five years.**
- [ ] **Step 2: Add owner-scoped repository selection that decrypts jobs and filters by terminal state plus `updated_at` cutoff without exposing raw SQL payloads.**
- [ ] **Step 3: Add `RetentionService.cleanup(owner_id, now)` that calls `UploadService.delete_import` for each selected job and returns counts and deleted IDs.**
- [ ] **Step 4: Run the focused tests and make them pass without deleting non-terminal or newer jobs.**
- [ ] **Step 5: Invoke one cleanup pass during `Application.from_config` only when `raw_retention_seconds > 0`; keep startup failures explicit rather than silently continuing.**

### Task 4: Document the effective policy and regression behavior

**Files:**
- Modify: `README.md`
- Modify: `docs/chat_import_guide.md`
- Modify: `docs/privacy_policy.md`
- Modify: `tests/integration/test_privacy_policy_contract.py`
- Modify: `tests/integration/test_http_api.py`

- [ ] **Step 1: Document the opt-in environment variable, terminal states covered, and the deliberate exclusion of `uploaded`/`processing`/`completed`.**
- [ ] **Step 2: Add an integration test proving a stale cancelled import is cleaned on application startup while another owner's and an active owner's import remain.**
- [ ] **Step 3: Update the privacy contract to state that normalized-data retention and successful-normalization cleanup remain future work.**
- [ ] **Step 4: Run focused service, HTTP, config, and privacy tests.**

### Task 5: Full verification and delivery checkpoint

**Files:**
- No additional source files.

- [ ] **Step 1: Run `npm test`.**
- [ ] **Step 2: Run `python -m compileall -q src tests` and `git diff --check`.**
- [ ] **Step 3: Run `codegraph sync` and `codegraph status` with escalation; verify the index is current.**
- [ ] **Step 4: Review the diff and commit the feature branch.**
- [ ] **Step 5: Report black-box acceptance evidence and wait for confirmation before merging `main`, rerunning the full suite, and pushing GitHub.**

### Scope gaps kept explicit

- This task does not delete `uploaded`, `processing`, or `completed` jobs because no successful-normalization event is persisted yet.
- This task does not add account-wide deletion, audit records, provider-side deletion, or normalized-message retention; those remain separate production tasks.
