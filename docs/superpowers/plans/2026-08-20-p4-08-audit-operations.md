# P4-08 Audit Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an encrypted, append-only, owner-scoped business audit trail for completed destructive and authorization-sensitive operations, with a redacted read API.

**Architecture:** Introduce a small `AuditEvent` domain value object and repository backed by a new forward-only metadata migration. Event payloads are encrypted with authenticated data (owner and event ID), while only bounded routing/index fields remain queryable. The application writes events after successful persona/import deletion, consent revocation/authorization, and training cancellation; the HTTP layer exposes owner-scoped read-only pagination. This slice deliberately does not claim immutable/WORM storage, external SIEM export, billing, monitoring, or audit of failed/unauthorized requests (those remain structured transport logs and later platform work).

**Tech Stack:** Python 3, SQLite/PostgreSQL metadata adapters, AES-GCM authenticated encryption, `unittest`, existing strict HTTP adapter.

---

### Task 1: Define the audit event contract and redaction rules

**Files:**
- Create: `src/domain/audit_events.py`
- Create: `tests/unit/test_audit_events.py`

- [x] **Step 1: Write failing tests**

Cover valid event construction, canonical action/outcome values, bounded identifiers/timestamps, rejection of raw content/secrets/path-like metadata keys, recursive mapping rejection, and immutable JSON-safe metadata.

- [x] **Step 2: Run the focused tests and verify the expected failure**

Run `python -m unittest tests.unit.test_audit_events -v` from the feature worktree. It must fail because the domain module does not exist yet.

- [x] **Step 3: Implement the minimal immutable value object**

Define `AuditAction` values `persona_deleted`, `import_deleted`, `consent_revoked`, `consent_authorized`, and `training_cancelled`; `AuditOutcome.SUCCESS` only for this slice; and frozen/slotted `AuditEvent` with `id`, `owner_id`, `action`, `resource_type`, `resource_id`, `occurred_at`, `metadata`. Normalize UTC ISO timestamps and deep-copy to immutable mappings. Permit only a fixed safe metadata key set (`deleted_children`, `provider_id`, `model_id`, `scope`, `reason_code`) with scalar bounded values; reject keys containing token/content/path/secret/provider-key markers and reject nested objects/lists so raw payloads cannot enter by accident.

- [x] **Step 4: Re-run the focused tests**

Run the same command and require all tests to pass.

- [x] **Step 5: Commit the contract**

Run `git add src/domain/audit_events.py tests/unit/test_audit_events.py && git commit -m "feat: define redacted audit event contract"`.

### Task 2: Persist encrypted append-only events and migrate both metadata backends

**Files:**
- Modify: `src/services/database.py`
- Modify: `src/services/postgresql_database.py`
- Create: `src/services/audit_repository.py`
- Create: `tests/unit/test_audit_repository.py`
- Modify: `tests/unit/test_database_migrations.py`
- Modify: `tests/unit/test_postgresql_migrations.py`

- [x] **Step 1: Add failing repository and migration tests**

Assert migration v12 creates `audit_events` with owner/action/resource indexes, empty-to-current upgrade preserves v1-v11 records, repository `append` encrypts payload (clear-text metadata is absent from the SQLite bytes), `list` filters strictly by owner, orders newest first, honors bounded limits, and corruption raises a stable redacted repository error. Add PostgreSQL migration SQL coverage using the existing fake connection/migrator tests.

- [x] **Step 2: Run tests to verify they fail**

Run `python -m unittest tests.unit.test_audit_repository tests.unit.test_database_migrations tests.unit.test_postgresql_migrations -v`; failures should identify missing migration/repository behavior.

- [x] **Step 3: Implement migration v12 and repository**

Append `audit_events` with UUID-like text ID, non-null owner/action/resource/index fields, UTC timestamp, encrypted payload, and `(owner_id, occurred_at, id)` index. Keep earlier migration checksums unchanged. Implement `AuditRepository.append(event)` as one immediate transaction and `list(owner_id, limit=100, before=None)` with a hard maximum of 100, decrypting each row using AAD `past-partner/audit-event/v1/{owner}/{event_id}`. Map malformed/corrupt rows to `AuditRepositoryError("audit_record_corrupt", "audit record is invalid")`; never include payload, key, or path details in errors.

- [x] **Step 4: Run migration/repository tests**

Run the focused command again and require green SQLite and PostgreSQL contract tests.

- [x] **Step 5: Commit persistence**

Run `git add src/services/database.py src/services/postgresql_database.py src/services/audit_repository.py tests/unit/test_audit_repository.py tests/unit/test_database_migrations.py tests/unit/test_postgresql_migrations.py && git commit -m "feat: persist encrypted owner audit events"`.

### Task 3: Wire successful business operations to the audit service

**Files:**
- Modify: `src/server/application.py`
- Modify: `tests/unit/test_application_audit.py`
- Modify: `tests/integration/test_http_api.py`

- [x] **Step 1: Write failing integration tests**

Exercise the existing authenticated API to delete a persona/import, revoke/authorize consent, and cancel a training job, then assert one redacted event per successful operation. Assert a failed/not-found mutation does not fabricate a success event and another owner cannot observe the first owner’s events.

- [x] **Step 2: Run tests and verify red**

Run `python -m unittest tests.unit.test_application_audit tests.integration.test_http_api -v`; the new assertions must fail because `Application` has no audit repository wiring.

- [x] **Step 3: Wire one shared `AuditRepository` into `Application`**

Construct it in `Application.from_config` beside the other metadata repositories, accept it as an injected constructor dependency for tests, and call `append` only after the wrapped operation returns successfully. Store only resource IDs, provider/model IDs, consent scope, and bounded child-deletion counts. If audit persistence fails, raise a stable `AuditServiceError("audit_unavailable", "audit record could not be persisted")` after the operation has completed; do not expose backend details. Keep the existing business response unchanged.

- [x] **Step 4: Re-run focused application/API tests**

Require all new and existing HTTP API tests to pass, including unchanged 404 behavior for missing resources.

- [x] **Step 5: Commit operation wiring**

Run `git add src/server/application.py tests/unit/test_application_audit.py tests/integration/test_http_api.py && git commit -m "feat: audit successful sensitive operations"`.

### Task 4: Expose a read-only owner-scoped audit endpoint and document the boundary

**Files:**
- Modify: `src/server/http.py`
- Modify: `src/server/application.py`
- Create: `tests/integration/test_http_audit.py`
- Modify: `README.md`
- Modify: `docs/privacy_policy.md`

- [x] **Step 1: Write failing endpoint tests**

Assert `GET /api/v1/audit-events` requires `owner:read`, returns `{audit_events: [...]}`, supports `limit` (1-100) and opaque `before` cursor, rejects invalid limits/cursors without leaking values, and never accepts POST/PATCH/DELETE. Verify the response contains no encrypted payload, raw content, filesystem paths, token fields, or provider secrets.

- [x] **Step 2: Run the endpoint tests and verify red**

Run `python -m unittest tests.integration.test_http_audit -v` and confirm the route is not implemented.

- [x] **Step 3: Implement the route and API facade**

Add a strict route constant, parse/validate query parameters, call `Application.list_audit_events(owner_id, limit, before)`, and map audit backend failures to HTTP 503 with stable `audit_unavailable`. Keep `owner:read` scope enforcement and existing CORS/logging behavior.

- [x] **Step 4: Document the exact P4-08 boundary**

Document encrypted owner-scoped successful-operation events, 100-record page cap, retention/backend limits, and the explicit exclusions: failed/unauthorized request audit, raw content, provider-side copies, WORM/compliance export, billing, and monitoring.

- [x] **Step 5: Run endpoint tests**

Run `python -m unittest tests.integration.test_http_audit tests.unit.test_http_logging -v` and require green.

- [x] **Step 6: Commit the API/docs**

Run `git add src/server/http.py src/server/application.py tests/integration/test_http_audit.py README.md docs/privacy_policy.md && git commit -m "feat: expose owner scoped audit events"`.

### Task 5: Full verification and acceptance handoff

**Files:**
- No new source files; update plan checkboxes only.

- [x] **Step 1: Run focused regression**

`python -m unittest tests.unit.test_audit_events tests.unit.test_audit_repository tests.unit.test_application_audit tests.integration.test_http_audit tests.integration.test_http_api tests.unit.test_database_migrations tests.unit.test_postgresql_migrations tests.unit.test_http_logging -v`

- [x] **Step 2: Run repository-wide checks**

Set `TEMP` and `TMP` to `E:\\CodexCaches\\Temp`, then run `python -m unittest discover -s tests -p "test*.py" -v`, `npm run test:web`, `python -m compileall src`, and `git diff --check`.

- [x] **Step 3: Sync CodeGraph and commit verification metadata**

Run `codegraph sync` with the required elevated permissions, inspect status, and ensure only the intended feature files are changed. Commit any final plan/doc updates separately.

- [x] **Step 4: Handoff for user acceptance**

Report branch, commit IDs, focused/full test counts, endpoint contract, and the explicit boundary. Do not merge or push until the user explicitly accepts P4-08.

## Acceptance boundary

P4-08 is accepted when encrypted events for the listed successful operations are durable, owner-isolated, redacted by construction, queryable through the read-only endpoint, and all focused/full regressions pass. It is not acceptance of compliance-grade immutable audit, provider-side deletion, billing, monitoring, or store release work.
