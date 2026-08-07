# P0-29 Persona Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an owner-scoped `DELETE /api/v1/personas/{persona_id}` workflow that removes the persona and all import artifacts controlled by the service.

**Architecture:** Keep the existing HTTP/Application/Service/Repository layering. `Application.delete_persona` coordinates owner-scoped import cleanup through `UploadService` and then removes the encrypted persona row through `PersonaService`; this preserves the existing `UploadService -> ImportService -> PersonaService` dependency direction. No account-wide deletion, provider-side deletion, retention scheduler, or export archive is added in this task.

**Tech Stack:** Python standard-library HTTP server, encrypted SQLite repositories, AES-GCM object storage, `unittest`, npm test wrapper, CodeGraph.

**Current audit status (2026-08-07):** The original persona-deletion implementation is present on `main`. This plan is retained as the P0-29 traceability record; the historical branch name is normalized below and the cascade now preflights processing imports to avoid partial deletion.

---

### Task 1: Branch, baseline, and red tests

**Files:**
- Create: `docs/superpowers/plans/2026-08-05-persona-deletion.md`
- Test: `tests/integration/test_http_api.py`
- Test: `tests/unit/test_persona_service.py`

- [ ] Create `feature/p0-29-persona-deletion` from the verified `main` commit.
- [ ] Run `npm test` on the clean baseline and record the result before changing production code.
- [ ] Add integration tests for: completed import deletion through persona deletion, incomplete chunk cleanup, and unknown persona `404`; cover owner isolation at the service layer because the current local-auth schema exposes one owner and enforces a foreign key on `local_users`.
- [ ] Add a service-level test proving deletion delegates only to the requested owner scope.
- [ ] Run the focused tests and confirm they fail because the DELETE route/service does not exist.

### Task 2: Persistence and service cascade

**Files:**
- Modify: `src/services/persona_repository.py`
- Modify: `src/services/persona_service.py`
- Modify: `src/services/import_repository.py`
- Modify: `src/services/import_service.py`
- Modify: `src/services/upload_service.py`

- [ ] Add owner-scoped repository deletion methods that return whether a row was deleted.
- [ ] Add a method to list owner-owned import manifests for a persona without exposing plaintext payloads.
- [ ] Add `UploadService.delete_persona_imports` to remove every manifest-listed chunk and completed payload, reject processing jobs, and remove each import row.
- [ ] Add `PersonaService.delete` to remove the encrypted persona row only after the application has verified ownership and removed its import artifacts.
- [ ] Preserve transaction rollback and explicit domain errors when a filesystem or metadata deletion fails.
- [ ] Run the focused unit and integration tests until green.

### Task 3: HTTP facade and contract documentation

**Files:**
- Modify: `src/server/application.py`
- Modify: `src/server/http.py`
- Modify: `README.md`
- Modify: `docs/chat_import_guide.md`
- Modify: `docs/privacy_policy.md`
- Modify: `tests/integration/test_privacy_policy_contract.py`

- [ ] Add `Application.delete_persona` to verify ownership, call `UploadService.delete_persona_imports`, delete the persona row, and return `{persona_id, deleted: true, deleted_imports: N}`.
- [ ] Map missing personas to `404`, in-progress deletion to `409`, and cleanup failures to an explicit `500` error code.
- [ ] Document that persona deletion clears controlled raw imports, normalized import metadata, and encrypted upload objects, while provider-side copies and account-wide retention/export remain outside this release.
- [ ] Update privacy contract assertions to match the actual behavior.

### Task 4: Verification and release

**Files:**
- No additional files beyond Tasks 1-3.

- [ ] Run focused deletion tests, privacy contract tests, `python -m compileall -q src tests`, and `git diff --check`.
- [ ] Run full `npm test` on the feature branch.
- [ ] Run escalated `codegraph sync` and `codegraph status`; confirm the index is current.
- [ ] Commit as `feat: add persona deletion cascade`, push the feature branch, fast-forward merge into `main`, rerun full `npm test`, and push `main`.
- [ ] Verify clean `main`, commit hash, and matching remote refs for `main` and the feature branch.
