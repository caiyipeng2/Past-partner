# R1-04 Worker Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task.

**Goal:** Persist a bounded, redacted worker lifecycle signal in the shared metadata backend and provide deterministic internal alert evaluation for stale workers and high failure rates.

**Architecture:** `TaskWorker` emits a small `WorkerObservation` after each poll. `WorkerObservability` validates and stores observations in migration 18, pruning by age and per-worker count. Alert evaluation reads only those sanitized rows and returns bounded `WorkerAlert` values; it does not expose an HTTP endpoint, provider credentials, payloads, owner IDs, paths, or exception text. The worker CLI wires the service when a shared metadata store is available, while tests can inject a sink without changing queue behavior.

**Tech Stack:** Python 3 standard library, SQLite/PostgreSQL metadata adapters, existing migration ledger, unittest.

### Task 1: Define redacted observation and alert domain values

- Create `src/domain/worker_observability.py`.
- Create `tests/unit/test_worker_observability.py`.
- Add tests for bounded worker/task identifiers, allowed outcomes, UTC timestamps, duration limits, stable failure-code normalization, and alert values that contain counts only.
- Verify a secret-like handler message, owner ID, path, and payload value cannot be represented by the domain model.

### Task 2: Add durable observation migration and repository service

- Modify `src/services/database.py` with append-only migration 18 for `worker_observations` and bounded indexes.
- Update migration assertions in `tests/unit/test_database_migrations.py` for version 18 and the new table.
- Implement `src/services/worker_observability.py` with `record`, bounded pruning, recent-row reads, and deterministic alert evaluation.
- Cover SQLite persistence, retention/count caps, concurrent-safe transactions, and stable metadata failures; keep the same SQL vocabulary usable by PostgreSQL.

### Task 3: Emit lifecycle observations from the worker

- Modify `src/services/task_worker.py` to emit only idle/success/retryable-failure/terminal-failure/lease-lost outcomes with bounded duration and failure code.
- Modify `src/worker/__init__.py` to accept an optional observer and keep observer failures from changing queue outcomes.
- Modify `src/worker/__main__.py` to construct the observer from the application metadata store without logging or exposing persisted rows.
- Add tests proving handler exceptions, retry codes, task payloads, owner IDs, and provider-like text never reach the observer.

### Task 4: Documentation and verification

- Update `README.md`, `docs/ROADMAP.md`, and `docs/privacy_policy.md` to describe persisted redacted worker observations and internal alert evaluation, while explicitly excluding external exporters, tracing, log shipping, SIEM, and client routes.
- Run focused worker/migration/observability tests, then the full Python and Node regression suites.
- Run `git diff --check`, CodeGraph sync/status, and a read-only review for data leakage and migration compatibility.

Each task is independently testable. Do not merge or push until the user accepts the branch result.
