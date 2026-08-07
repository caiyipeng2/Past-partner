# P0-28 Data Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authenticated owner-scoped data export endpoint for the currently persisted persona, import, mapping, and correction metadata.

**Architecture:** Reuse the existing encrypted SQLite repositories and application facade. The endpoint returns a versioned JSON document containing decrypted metadata that the authenticated owner can restore or inspect; raw encrypted upload payloads are deliberately omitted from this bounded JSON slice and reported in the response scope. No account-level export, third-party provider data, or 3 GiB in-memory buffering is introduced.

**Tech Stack:** Python standard-library HTTP server, encrypted SQLite repositories, `unittest`, npm test wrapper, CodeGraph.

**Current audit status (2026-08-07):** The implementation and tests from the original data-export slice are already present on `main`. This plan is retained as the P0-28 traceability record; the historical branch name below is normalized to the current numbering so progress is not reported under P0-24.

---

### Task 1: Branch, baseline, and red tests

**Files:**
- Create: `docs/superpowers/plans/2026-08-05-data-export.md`
- Test: `tests/integration/test_http_api.py`
- Test: `tests/unit/test_import_repository.py`

- [ ] Create `feature/p0-28-data-export` from the verified `main` commit.
- [ ] Run the baseline `npm test` before production edits.
- [ ] Add an integration test that creates a persona, completes an import, saves participant mapping and corrections, then expects `GET /api/v1/data-export` to return a versioned owner export containing all metadata and an explicit `raw_payloads_included: false` scope marker.
- [ ] Add an integration test that proves an invalid bearer token cannot export data and an empty owner export returns empty arrays.
- [ ] Add a repository test for owner-scoped import listing.
- [ ] Run focused tests and confirm they fail because the route and repository listing do not exist.

### Task 2: Repository and application export

**Files:**
- Modify: `src/services/import_repository.py`
- Modify: `src/services/import_service.py`
- Modify: `src/server/application.py`

- [ ] Add `ImportRepository.list(owner_id)` that decrypts and returns only the requested owner's import jobs sorted by creation time and ID.
- [ ] Add the matching `ImportService.list(owner_id)` facade.
- [ ] Add `Application.export_data(owner_id)` returning `export_version`, `generated_at`, `personas`, `imports`, and `scope` fields; each import includes its decrypted job and manifest metadata.
- [ ] Keep raw payload bytes out of the JSON response and set `scope.raw_payloads_included` to `false` with an explicit omission reason.

### Task 3: HTTP contract and documentation

**Files:**
- Modify: `src/server/http.py`
- Modify: `README.md`
- Modify: `docs/privacy_policy.md`
- Modify: `docs/chat_import_guide.md`
- Modify: `tests/integration/test_privacy_policy_contract.py`

- [ ] Route `GET /api/v1/data-export` through the existing owner authentication path.
- [ ] Return `404` for no route only; valid owners always receive a JSON export, including empty arrays.
- [ ] Document that this release exports persisted metadata, mappings, corrections, and manifests, while raw payload streaming, account-wide export, provider-side copies, and audit records remain outside this slice.
- [ ] Keep privacy-policy claims synchronized with the actual endpoint behavior.

### Task 4: Verification and release

**Files:**
- No additional files beyond Tasks 1-3.

- [ ] Run focused export and privacy tests, compileall, and `git diff --check`.
- [ ] Run full `npm test` on the feature branch.
- [ ] Run escalated `codegraph sync` and `codegraph status`.
- [ ] Commit as `feat: add owner data export`, push the feature branch, fast-forward merge into `main`, rerun full `npm test`, and push `main` after explicit release confirmation if the protected branch requests it.
- [ ] Verify clean `main`, commit hash, and matching remote refs.
