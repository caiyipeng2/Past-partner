# R1-03 Tenant Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded, admin-only tenant member inventory and same-tenant session revocation surface without introducing local registration, passwords, recovery codes, or cross-tenant resource access.

**Architecture:** Keep `owner_id` as the resource boundary and use the authenticated principal's immutable `tenant_id` and `role` only for tenant administration. The auth service will return redacted member metadata from `local_identities` after validating the encrypted account record, and will reuse the existing transactional revoke-all implementation for a same-tenant member. HTTP handlers enforce `owner:read` for listing and `owner:write` for revocation before the application checks admin role and tenant membership.

**Tech Stack:** Python standard-library HTTP server, SQLite/PostgreSQL `MetadataStore`, encrypted local auth records, `unittest`, existing integration HTTP harness.

---

### Task 1: Lock the tenant administration contract with failing tests

**Files:**
- Modify: `tests/unit/test_local_auth.py`
- Create: `tests/integration/test_http_tenant_management.py`

- [x] **Step 1: Write unit tests for same-tenant listing and revocation**
  Create an admin and two members in separate tenants, issue account sessions, assert the admin list contains only same-tenant members with `user_id`, `subject`, `tenant_id`, `role`, and `issuer`, and assert revocation rejects a cross-tenant target while removing same-tenant access and refresh sessions.

- [x] **Step 2: Write HTTP tests for scope, role, tenant, and redaction boundaries**
  Add authenticated requests for `GET /api/v1/tenant/members` and `POST /api/v1/tenant/members/{user_id}/revoke-sessions`; assert admin success, member `403`, read-only scope `403` for revoke, cross-tenant target `404`, bounded fields, and no encrypted payload or token fields.

- [x] **Step 3: Run the focused tests to verify RED**
  Run `python -m unittest tests.unit.test_local_auth tests.integration.test_http_tenant_management -v`.
  Expected: failures because the tenant member service methods and routes do not exist.

### Task 2: Implement the auth-service tenant member boundary

**Files:**
- Modify: `src/services/local_auth.py`

- [x] **Step 1: Add bounded member listing**
  Add `list_tenant_members(actor_user_id, *, limit=100)` that loads the actor, requires an admin role, validates the limit, queries only `local_users.kind = 'member'` and `local_identities.role = 'member'` rows for the actor's tenant, validates each encrypted account through `_load_identity`, and returns only bounded identity metadata. Map store failures to `tenant_members_unavailable` and malformed records to `tenant_member_invalid`.

- [x] **Step 2: Add same-tenant member session revocation**
  Add `revoke_tenant_member_sessions(actor_user_id, target_user_id)` that loads both identities, requires the actor role to be `admin`, requires equal tenant IDs, requires a member target, then delegates deletion to `revoke_all_sessions`. Cross-tenant and non-member targets return `tenant_member_not_found` without revealing existence.

- [x] **Step 3: Run focused unit tests to verify GREEN**
  Run `python -m unittest tests.unit.test_local_auth -v` and confirm the new auth tests pass without changing existing OIDC/session behavior.

### Task 3: Expose authenticated HTTP and application routes

**Files:**
- Modify: `src/server/application.py`
- Modify: `src/server/http.py`
- Modify: `tests/integration/test_http_tenant_management.py`

- [x] **Step 1: Add application wrappers**
  Add wrappers that accept `OwnerPrincipal`, preserve the principal's tenant and role, and delegate to the auth service. Do not accept arbitrary tenant IDs from request bodies.

- [x] **Step 2: Add route templates and stable status mapping**
  Add the GET list route and POST revoke route, enforce existing auth scopes through `_requires_auth`, parse a bounded `limit` query parameter, and map tenant admin failures to `403`/`404`/`503` without exposing database or encrypted record details.

- [x] **Step 3: Run HTTP-focused tests**
  Run `python -m unittest tests.integration.test_http_tenant_management tests.integration.test_http_operations -v`.

### Task 4: Document the staged tenant boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/privacy_policy.md`

- [x] **Step 1: Document endpoints and non-goals**
  State that only admin principals can inspect same-tenant member metadata or revoke same-tenant member sessions; no local registration, password recovery, tenant creation, billing administration, or third-party IdP logout is provided.

- [x] **Step 2: Add documentation contract assertions**
  Extend the existing documentation tests so the route, scope, redaction, and non-goal language cannot drift.

### Task 5: Full verification and handoff

**Files:**
- No additional files.

- [x] **Step 1: Sync CodeGraph and run static checks**
  Run `codegraph sync`, `git diff --check`, and `python -m compileall -q src tests`.

- [x] **Step 2: Run the full verification suite**
  Run `npm test` and `flutter test` from `mobile/`.

- [ ] **Step 3: Commit the feature branch and wait for user acceptance**
  Commit with `feat: add tenant member administration`, report the branch and evidence, and do not merge or push until acceptance.
