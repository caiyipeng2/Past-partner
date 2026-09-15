# R1-03 Tenant Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a same-tenant administrator update the bounded tenant display name without adding registration, password recovery, Provider calls, or cross-tenant mutation.

**Architecture:** Reuse the existing `local_tenants.display_name` registry row. The service loads and validates the actor identity, locks the tenant row in the existing metadata transaction, updates only the actor's tenant, and returns bounded tenant metadata. HTTP exposes `PATCH /api/v1/tenant`; existing authentication, `owner:write` scope, admin role, stable error mapping, and redacted logging remain in force.

**Tech Stack:** Python standard-library HTTP server, SQLite/PostgreSQL `MetadataStore`, encrypted local auth identities, `unittest`, CodeGraph, Flutter/Dart regression checks.

---

### Task 1: Define the contract with failing tests

**Files:**
- Modify: `tests/unit/test_local_auth.py`
- Modify: `tests/integration/test_http_tenant_management.py`
- Modify: `tests/integration/test_privacy_policy_contract.py`

- [x] Add a unit test proving an admin can rename the current tenant, that the new name is returned by `get_tenant`, and that a suspended tenant remains editable by its admin.
- [x] Add unit tests for non-admin rejection, blank/overlong/non-string names, and cross-tenant isolation.
- [x] Add an HTTP test for `PATCH /api/v1/tenant`, including the `owner:write` scope requirement and stable validation errors.
- [x] Add the route and boundary wording to the privacy contract test.
- [x] Run the focused tests and confirm they fail because the service/application/route method is absent.

### Task 2: Implement the service and HTTP surface

**Files:**
- Modify: `src/services/local_auth.py`
- Modify: `src/server/application.py`
- Modify: `src/server/http.py`

- [x] Add bounded display-name validation using the existing identifier limits: trim surrounding whitespace, reject empty values and values over 128 characters, and map all invalid values to `tenant_display_name_invalid`.
- [x] Add `update_tenant_display_name(actor_user_id, display_name)` with admin-only authorization, a transactional tenant-row lock for PostgreSQL, and an update limited to the actor's tenant.
- [x] Preserve admin recovery while suspended and return `tenant_id`, `display_name`, `status`, `created_at`, `member_count`, and `active_member_count` through the existing tenant metadata shape.
- [x] Wire `PATCH /api/v1/tenant` through the application and HTTP route template; require `owner:write` through existing scope enforcement and never include credentials or raw content in responses/logs.
- [x] Run the focused tests to GREEN.

### Task 3: Document and verify the slice

**Files:**
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/privacy_policy.md`

- [x] Document `PATCH /api/v1/tenant`, admin-only same-tenant behavior, bounded display-name semantics, and explicit non-goals (registration, password recovery, Provider operations, billing).
- [x] Run `git diff --check`, `python -m compileall -q src tests`, and `codegraph sync`.
- [x] Run the full repository `npm test`; do not run real Provider smoke or external Provider calls.
- [x] Mobile files are unchanged; prior Flutter/Dart checks remain the applicable baseline, and no real Provider smoke was run for this backend-only slice.

### Task 4: Review and handoff

**Files:**
- No additional files.

- [ ] Complete an independent review focused on admin recovery, tenant isolation, scope enforcement, and PostgreSQL transaction behavior.
- [ ] Commit as `feat: add tenant profile update` and wait for user acceptance before merging or pushing.
