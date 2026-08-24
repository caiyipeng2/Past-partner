# R1-03 Account Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a durable multi-account identity boundary so local sessions can represent distinct subjects, tenants, and roles without weakening the existing owner-scoped resource queries.

**Architecture:** Keep `owner_id` as the resource ownership key for backwards compatibility, but allow more than one local user. A new `local_identities` table stores the queryable subject, tenant, and role mapping; encrypted `local_users` records remain the source of truth for account payload integrity. `OwnerPrincipal` gains account metadata while retaining its existing name and constructor compatibility. This slice exposes only service-level local account creation for deterministic development/test use; production OIDC/OAuth token verification remains a later R1-03 slice and is fail-closed.

**Tech Stack:** Python 3, SQLite versioned migrations, unittest, existing AES-GCM authenticated metadata encryption.

---

### Task 1: Add the account identity migration

**Files:**
- Modify: `src/services/database.py`
- Modify: `tests/unit/test_database_migrations.py`

- [ ] **Step 1: Write migration assertions**

Add tests that migrate a fresh database to version 16, assert `local_identities` has `user_id`, `tenant_id`, `subject`, `role`, and `created_at`, and assert an upgrade from version 15 preserves existing `local_users` and all owner-scoped rows.

- [ ] **Step 2: Run the focused migration tests**

Run: `python -m unittest tests.unit.test_database_migrations -v`

Expected: FAIL because the current schema stops at version 15 and has no identity table.

- [ ] **Step 3: Implement migration 16**

Rebuild `local_users` with `kind IN ('owner', 'member')` while preserving IDs and encrypted payloads, then add `local_identities` with unique `subject`, tenant/role checks, and a foreign key to `local_users`. Keep the migration append-only and update `CURRENT_SCHEMA_VERSION` through the existing tuple.

- [ ] **Step 4: Run the migration tests again**

Run: `python -m unittest tests.unit.test_database_migrations -v`

Expected: PASS, including the version-15 upgrade preservation case.

- [ ] **Step 5: Commit the schema slice**

```powershell
git add src/services/database.py tests/unit/test_database_migrations.py
git commit -m "feat: add multi-account identity schema"
```

### Task 2: Add encrypted local account records and principal metadata

**Files:**
- Modify: `src/services/local_auth.py`
- Modify: `tests/unit/test_local_auth.py`

- [ ] **Step 1: Write failing account tests**

Cover creating two distinct subjects, rejecting duplicate subjects, issuing sessions for each account, returning tenant/role/subject on authentication, and rejecting one account from a resource stored under the other account’s `owner_id`.

- [ ] **Step 2: Run the focused auth tests**

Run: `python -m unittest tests.unit.test_local_auth -v`

Expected: FAIL because the service can only bootstrap one owner and `OwnerPrincipal` has no account metadata.

- [ ] **Step 3: Implement the minimal account API**

Add `create_local_account(subject, tenant_id=None, role='member')` and `issue_account_session(user_id, remote_address='127.0.0.1', scopes=None)`. Restrict account creation and non-owner local sessions to `development`/`test`; validate non-empty bounded identifiers and roles; store the account payload encrypted; atomically insert the identity mapping and session. Keep owner bootstrap behavior unchanged.

Extend `OwnerPrincipal` with `tenant_id`, `subject`, and `role` defaults so existing callers remain valid. Make `authenticate` load and validate the identity mapping and encrypted account payload, failing closed for missing, mismatched, or malformed identity rows.

- [ ] **Step 4: Run focused auth and repository isolation tests**

Run: `python -m unittest tests.unit.test_local_auth tests.unit.test_persona_repository -v`

Expected: PASS, including all existing owner/device pairing tests and the new two-account isolation assertions.

- [ ] **Step 5: Commit the auth slice**

```powershell
git add src/services/local_auth.py tests/unit/test_local_auth.py
git commit -m "feat: support isolated local account principals"
```

### Task 3: Verify application boundary and document the staged scope

**Files:**
- Create: `tests/integration/test_multi_account_boundary.py`
- Modify: `docs/privacy_policy.md`
- Modify: `docs/ROADMAP.md`

- [ ] **Step 1: Write an HTTP/application isolation test**

Build one application over one metadata database, create two local accounts and sessions, create a persona with account A, and assert account B receives the existing not-found behavior for that persona and cannot delete it. Assert the response payloads contain no access token or encrypted account payload.

- [ ] **Step 2: Run the integration test to expose any missing propagation**

Run: `python -m unittest tests.integration.test_multi_account_boundary -v`

Expected: FAIL only if application/auth wiring drops the account principal or resource owner scope.

- [ ] **Step 3: Make the smallest wiring/documentation update**

Use the authenticated principal’s `user_id` as the existing owner scope, add no alternate unscoped query path, and document that this slice provides local development/test identity boundaries only; production OIDC/OAuth2, refresh tokens, account management, and admin/member APIs remain pending.

- [ ] **Step 4: Run the full regression suite**

Run: `$env:TEMP='D:\AI开发\.test-temp'; $env:TMP=$env:TEMP; New-Item -ItemType Directory -Force $env:TEMP | Out-Null; npm test`

Expected: all Python and Node tests pass; any external-provider or PostgreSQL suites remain skipped according to repository policy.

- [ ] **Step 5: Commit the integration/documentation slice**

```powershell
git add tests/integration/test_multi_account_boundary.py docs/privacy_policy.md docs/ROADMAP.md
git commit -m "test: verify multi-account resource isolation"
```

