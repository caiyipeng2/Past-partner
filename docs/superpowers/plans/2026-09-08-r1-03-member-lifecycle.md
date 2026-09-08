# R1-03 Member Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add same-tenant admin control to disable or reactivate member accounts without introducing passwords or local registration.

**Architecture:** Add a forward-only `account_status` column to `local_identities`, defaulting existing accounts to `active`. Authentication and token issuance fail closed for disabled accounts. A same-tenant admin updates a member status atomically; disabling also deletes that member's access sessions and OIDC refresh tokens in the same transaction.

**Tech Stack:** Python standard-library HTTP server, SQLite/PostgreSQL `MetadataStore`, AES-GCM local auth records, migration ledger, `unittest`, Flutter regression suite.

---

### Task 1: Define migration, auth, and HTTP red tests

**Files:**
- Modify: `tests/unit/test_database_migrations.py`
- Modify: `tests/unit/test_local_auth.py`
- Modify: `tests/integration/test_http_tenant_management.py`

- [x] Test migration v26 adds `account_status=active` and preserves existing identities.
- [x] Test disable/re-enable, disabled authentication rejection, session/refresh cleanup, last-admin/self/owner/cross-tenant boundaries, and HTTP scope/error responses.
- [x] Run focused tests and confirm RED.

### Task 2: Implement status migration and authentication enforcement

**Files:**
- Modify: `src/services/database.py`
- Modify: `src/services/local_auth.py`

- [x] Add migration 26 for `local_identities.account_status` with active/disabled constraint.
- [x] Include status in identity loads and reject disabled accounts for session issuance, bearer authentication, OIDC refresh, and member administration.
- [x] Add atomic `update_tenant_member_status` that updates status and deletes sessions/refresh tokens when disabling.
- [x] Run focused auth/migration tests to GREEN.

### Task 3: Expose route and documentation

**Files:**
- Modify: `src/server/application.py`
- Modify: `src/server/http.py`
- Modify: `tests/integration/test_http_tenant_management.py`
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/privacy_policy.md`
- Modify: `tests/integration/test_privacy_policy_contract.py`

- [x] Add `PATCH /api/v1/tenant/members/{user_id}/status` accepting `{"status":"active"|"disabled"}` with existing owner:write and same-tenant admin checks.
- [x] Map disabled/invalid/not-found/last-admin/backend failures to stable redacted status codes.
- [x] Document lifecycle semantics and non-goals; update contract assertions.

### Task 4: Verify and hand off

**Files:**
- No additional files.

- [x] Run CodeGraph sync, diff/compile checks, `npm test`, `flutter test`, and `dart analyze`.
- [ ] Commit `feat: add tenant member lifecycle` and wait for user acceptance before merge/push.
