# R1-03 Tenant Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded tenant registry and same-tenant admin suspend/reactivate controls without introducing password recovery, local registration, or provider-side account operations.

**Architecture:** Keep tenant identity in the existing encrypted account records and `local_identities.tenant_id`, with a new encrypted-free lifecycle registry keyed by tenant ID and a bounded display name. Existing accounts are backfilled into active tenant rows. Tenant suspension is checked during member session issuance/authentication and OIDC refresh; an atomic status change revokes all member sessions and refresh tokens while preserving metadata for later reactivation.

**Tech Stack:** Python standard-library HTTP server, SQLite/PostgreSQL `MetadataStore`, migration ledger, AES-GCM local auth records, `unittest`, Flutter regression suite.

---

### Task 1: Define migration, tenant service, and HTTP red tests

**Files:**
- Modify: `tests/unit/test_database_migrations.py`
- Modify: `tests/unit/test_local_auth.py`
- Modify: `tests/integration/test_http_tenant_management.py`

- [x] Test migration v27 creates tenant registry rows for existing accounts and preserves active defaults.
- [x] Test admin tenant status listing, suspend/reactivate, member access blocking, session cleanup, cross-tenant/owner/non-admin boundaries, and stable errors.
- [x] Run focused tests and confirm RED.

### Task 2: Implement tenant registry and enforcement

**Files:**
- Modify: `src/services/database.py`
- Modify: `src/services/local_auth.py`

- [x] Add migration 27 with bounded tenant display name/status and owner-scoped tenant uniqueness.
- [x] Backfill/update registry rows when local or OIDC accounts are created.
- [x] Add admin-only tenant status read/update methods with atomic suspend cleanup and active checks in session/auth/refresh paths.
- [x] Run focused tests to GREEN.

### Task 3: Expose HTTP routes and document boundaries

**Files:**
- Modify: `src/server/application.py`
- Modify: `src/server/http.py`
- Modify: `tests/integration/test_http_tenant_management.py`
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/privacy_policy.md`
- Modify: `tests/integration/test_privacy_policy_contract.py`

- [x] Add `GET /api/v1/tenant` and `PATCH /api/v1/tenant/status` with existing scope and admin enforcement.
- [x] Map unknown, invalid, suspended, and backend errors to stable redacted responses.
- [x] Document tenant lifecycle semantics and non-goals.

### Task 4: Verify and hand off

**Files:**
- No additional files.

- [x] Run CodeGraph sync, diff/compile checks, `npm test`, `flutter test`, and `dart analyze`.
- [ ] Commit `feat: add tenant lifecycle controls` and wait for user acceptance before merge/push.
