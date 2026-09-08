# R1-03 Tenant Role Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a same-tenant admin change a member's `member`/`admin` role while preserving encrypted identity consistency and preventing self-lockout, owner mutation, cross-tenant disclosure, and removal of the last admin.

**Architecture:** Reuse `local_identities` as the queryable tenant/role index and the encrypted `local_users` payload as the integrity source. Perform role changes in one metadata transaction, re-encrypting the target account payload and updating the role index atomically. Expose a scope-protected HTTP PATCH route with stable redacted errors.

**Tech Stack:** Python standard-library HTTP server, SQLite/PostgreSQL `MetadataStore`, AES-GCM local auth records, `unittest`, Flutter regression suite.

---

### Task 1: Define failing auth and HTTP contracts

**Files:**
- Modify: `tests/unit/test_local_auth.py`
- Modify: `tests/integration/test_http_tenant_management.py`

- [x] Add tests for promotion/demotion, self-demotion rejection, last-admin protection, cross-tenant/owner target hiding, member and read-only scope rejection, and encrypted payload role consistency.
- [x] Run focused tests and verify failures because the role-management method and route do not exist.

### Task 2: Implement atomic role updates

**Files:**
- Modify: `src/services/local_auth.py`

- [x] Add `update_tenant_member_role(actor_user_id, target_user_id, role)` with bounded role validation, same-tenant admin authorization, member-only target validation, last-admin protection, and one transaction updating both encrypted payload and identity index.
- [x] Map metadata failures and malformed records to stable non-sensitive errors.
- [x] Run focused auth tests to green.

### Task 3: Expose the HTTP route and documentation

**Files:**
- Modify: `src/server/application.py`
- Modify: `src/server/http.py`
- Modify: `tests/integration/test_http_tenant_management.py`
- Modify: `README.md`
- Modify: `docs/ROADMAP.md`
- Modify: `docs/privacy_policy.md`
- Modify: `tests/integration/test_privacy_policy_contract.py`

- [x] Add `PATCH /api/v1/tenant/members/{user_id}` with a JSON `role` body, existing `owner:write` enforcement, stable status mapping, and route-template redaction.
- [x] Document same-tenant role management and its non-goals; add documentation assertions.
- [x] Run focused HTTP and documentation tests.

### Task 4: Verify and hand off

**Files:**
- No additional files.

- [x] Run CodeGraph sync, diff/compile checks, `npm test`, `flutter test`, and `dart analyze`.
- [ ] Commit `feat: add tenant role management` and wait for user acceptance before merge/push.
