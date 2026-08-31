# R2-04 Commercial And Compliance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add auditable commercial and compliance foundations without claiming a live payment gateway, regulatory WORM storage, or a complete operations console before those integrations exist.

**Architecture:** Build the feature as small owner-scoped slices on top of the existing encrypted metadata store. The first slice adds an integer-minor-unit billing ledger with idempotent append-only entries and read-only balance/history APIs; later slices can attach a payment adapter, subscription entitlement service, immutable audit anchoring, data-subject notifications, and admin operations views without changing the ledger contract.

**Tech Stack:** Python standard library, existing AES-GCM metadata repositories, SQLite/PostgreSQL migration abstraction, Python `unittest`, versioned HTTP API, Flutter client in later slices.

---

### Task 1: Owner-scoped balance ledger foundation

**Files:**
- Create: `src/domain/billing.py`
- Create: `src/services/billing_repository.py`
- Create: `src/services/billing_service.py`
- Modify: `src/services/database.py`
- Modify: `src/server/application.py`
- Modify: `src/server/http.py`
- Create: `tests/unit/test_billing.py`
- Create: `tests/integration/test_http_billing.py`
- Modify: `tests/unit/test_database_migrations.py`
- Modify: `README.md`
- Modify: `docs/privacy_policy.md`

- [x] **Step 1: Write failing domain, repository, migration, and HTTP tests**
- [x] **Step 2: Run the focused tests and confirm the billing surface is missing**
- [x] **Step 3: Implement integer-minor-unit validation and encrypted append-only storage**
- [x] **Step 4: Add owner-read balance/history routes and deletion cascade coverage**
- [x] **Step 5: Run focused tests, compile checks, and CodeGraph synchronization**
- [x] **Step 6: Commit the slice and wait for user acceptance**

The first slice exposes only `GET /api/v1/billing/balance` and
`GET /api/v1/billing/entries`. Credits/debits are service-level operations for a
future verified payment or usage adapter; no client route can mint balance, and no
provider webhook or payment credential is accepted yet. Amounts are integer minor
units with one currency per owner account, entries are encrypted and idempotent by
an owner-scoped operation key, and an insufficient debit fails without a partial
write.

### Task 2: Subscription entitlement state

Add encrypted owner subscription records with explicit trial/active/past_due/cancelled
states, period boundaries, plan identifiers, and idempotent provider event keys. A
read-only owner route reports current entitlement; payment-provider webhooks remain a
separate adapter and are never trusted without signature verification.

### Task 3: Compliance-grade audit anchoring

Extend the existing redacted audit trail with a tamper-evident hash chain and
append-only verification command. Keep the current API read-only, expose chain gaps or
hash mismatches as stable operational errors, and document that an external WORM store
is still required for regulatory retention.

### Task 4: Data-subject notifications

Add encrypted, owner-scoped notification records for export/deletion lifecycle events,
with bounded delivery state and retry metadata. Notifications contain only operation
IDs, counts, timestamps, and stable error codes; raw chat, media, credentials, and
provider payloads remain excluded.

### Task 5: Operations read-only surface

Add a role-gated, redacted operations summary for administrators: bounded queue health,
billing reconciliation status, audit-chain health, notification failures, and recent
diagnostic IDs. No raw owner content or mutation endpoint is included.

### Verification boundary

Every task uses a separate `codex/` branch, focused tests before implementation,
full Python/Node/Flutter regression after acceptance, and an explicit statement of
which external payment, WORM, notification, or operations infrastructure was not run.
