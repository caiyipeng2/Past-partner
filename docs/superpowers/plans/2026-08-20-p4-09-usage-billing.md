# P4-09 Usage Billing Foundation Implementation Plan

**Goal:** Add the smallest production-platform billing slice required by the approved roadmap: record actual provider usage when a successful chat response supplies normalized usage, calculate a redacted server-side charge from the configured catalog price, and expose an owner-scoped read-only usage ledger.

**Boundary:** This task does not implement payment providers, subscriptions, invoices, refunds, credit balances, automatic blocking, tax, revenue settlement, or provider-side billing reconciliation. BYOK and local-compute operations remain visible as `provider_billed`/`local_compute` metadata without inventing a platform charge.

## Contract

- A usage record belongs to exactly one owner and one operation (`chat` in this slice).
- Only bounded fields are persisted: provider/model, billing mode, normalized token/media units, currency, charge state, calculated amount when pricing is available, provider request reference, and timestamp.
- The encrypted payload contains the complete validated record; plaintext columns are limited to owner, operation, provider/model, billing/charge state, occurred-at, the irreversible request fingerprint, record version, and ciphertext.
- Missing provider usage or missing price produces `usage_unavailable`/`pricing_unavailable` metadata and no fabricated amount.
- Duplicate provider request references are idempotent per owner; a retry cannot double-count a successful response.
- `GET /api/v1/usage` requires `owner:read`, supports bounded cursor pagination, and has no write/delete route.
- Raw messages, provider responses, credentials, source paths, and API keys never enter the ledger.

## Steps

- [x] Add immutable `UsageRecord`/validation and migration 13.
- [x] Add encrypted append-only `UsageRepository` with owner-scoped cursor pagination and idempotency.
- [x] Add a conversation usage hook that records only successful `ChatResponse.usage` data and preserves chat success when usage recording is unavailable.
- [x] Add the read-only HTTP endpoint and stable validation/error mapping.
- [x] Add unit/integration tests, update privacy/README boundary documentation, run focused and full regressions.

## Acceptance

P4-09 is ready for user acceptance when successful deterministic chat usage is recorded exactly once, records are encrypted and owner-isolated, missing usage/prices remain explicit and non-billable, the read endpoint enforces scopes and pagination, all focused/full regressions pass, and the documented exclusions remain true.
