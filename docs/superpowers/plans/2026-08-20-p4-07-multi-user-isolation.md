# P4-07 Multi-User Isolation Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn the existing owner-filtered local session into an explicit scoped-principal boundary that is ready for multiple users without weakening current data isolation.

**Architecture:** Keep the current local owner ID as the subject/account boundary for this slice. Add a canonical read/write scope set to authenticated sessions and principals, enforce read scope for safe reads and write scope for mutations at the HTTP boundary, and fail closed on malformed or unknown scopes. This is a foundation only: it does not claim OIDC registration, account administration, billing, or production identity federation.

**Compatibility:** Existing sessions upgraded from schema v10 receive the full local-development scope set. Existing `Application` and repository APIs continue to receive `owner_id`; no owner-filtered repository is allowed to broaden its query.

## Task 1: Define the scoped principal contract

**Files:**
- Create: `src/domain/access_scope.py`
- Modify: `src/services/local_auth.py`
- Create: `tests/unit/test_access_scope.py`
- Modify: `tests/unit/test_local_auth.py`

Add a small immutable scope value object with only `owner:read` and `owner:write`. It must reject wildcard, empty, duplicated, or unknown values and expose `allows(scope)` without accepting string-prefix tricks. Extend `OwnerPrincipal` with an immutable scope set and a `require(scope)` method that raises a redacted `insufficient_scope` error.

Write failing tests first for canonicalization, malformed scope rejection, read/write checks, and backward-compatible full scopes.

## Task 2: Persist session scopes with a forward-only migration

**Files:**
- Modify: `src/services/database.py`
- Modify: `tests/unit/test_database_migrations.py`
- Modify: `tests/unit/test_postgresql_migrations.py`
- Modify: `src/services/local_auth.py`
- Modify: `tests/unit/test_local_auth.py`

Append migration v11 `session_scopes` with a `scopes` text column defaulting to the canonical full scope string. Keep v1-v10 unchanged and update migration expectations and the v10 upgrade-preservation test. `issue_session` accepts an internal validated scope set, persists the canonical representation, and defaults to full local-owner scopes. `authenticate` parses and validates stored scopes; malformed or unknown values fail closed with the generic authentication error.

The migration must compile for SQLite and PostgreSQL through the existing adapters. No scope or token value may be echoed in errors or logs.

## Task 3: Enforce scopes at the HTTP boundary

**Files:**
- Modify: `src/server/http.py`
- Modify: `src/server/application.py`
- Create: `tests/integration/test_http_scopes.py`

Authenticate to a principal once per protected request. Require `owner:read` for `GET` and `owner:write` for `POST`, `PUT`, `PATCH`, and `DELETE`; keep the session-issue endpoint unauthenticated and preserve existing CORS behavior. Map insufficient scope to HTTP 403 with a stable `insufficient_scope` code and no scope/token details. The handler must still pass only the principal subject to existing owner-scoped services.

Test read-only sessions can read but cannot mutate, write-only sessions cannot read, full sessions preserve existing API behavior, and cross-owner resource IDs remain 404 rather than becoming an authorization oracle.

## Task 4: Document the isolation boundary and verify regressions

**Files:**
- Modify: `README.md`
- Modify: `docs/privacy_policy.md`
- Modify: `.env.example`

Document session scopes, the default local full-scope behavior, the 403 contract, and the explicit boundary that P4-07 does not implement account registration/OIDC/billing. Do not add client-configured tokens or secrets.

Run focused tests:

```powershell
python -m unittest tests.unit.test_access_scope tests.unit.test_local_auth tests.unit.test_database_migrations tests.unit.test_postgresql_migrations tests.integration.test_http_scopes -v
```

Run the full Python suite, Web tests, `py_compile`, `git diff --check`, and CodeGraph sync before committing. Do not merge or push until user acceptance.

## Commit and acceptance boundary

Commit the isolated branch as `feature/p4-07-multi-user-isolation` only after all tests pass. Report that this is the scoped-principal foundation, not full production identity federation, and wait for explicit acceptance before merging `main`, rerunning the merged suite, pushing `origin/main`, and cleaning the worktree.
