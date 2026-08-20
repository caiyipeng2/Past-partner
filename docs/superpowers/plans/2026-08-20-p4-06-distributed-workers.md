# P4-06 Distributed Task Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a durable, owner-scoped task queue and worker lease contract that can run on both SQLite and PostgreSQL without exposing task payloads or claiming work twice.

**Architecture:** Persist task metadata in the existing encrypted metadata database. Queue rows keep routing and lifecycle fields queryable, while task payloads are authenticated-encrypted with the existing master-key service. A `TaskWorker` claims one lease at a time, dispatches only registered task types, renews leases, and records bounded retryable failures; no broker, thread, or cloud SDK is introduced in this slice.

**Tech Stack:** Python 3.11+, `sqlite3`, optional `psycopg`, existing `MetadataStore`, authenticated encryption, `unittest`.

---

### Task 1: Add the task queue migration and domain contract

**Files:**
- Modify: `src/services/database.py`
- Create: `src/domain/task_queue.py`
- Test: `tests/unit/test_database_migrations.py`
- Test: `tests/unit/test_task_queue.py`

- [ ] **Step 1: Write failing migration and domain tests**

Add tests that assert schema version 10 contains `task_queue`, its claim index, and owner cascade; construct a queued task with a bounded task type/payload; reject empty or oversized task types, invalid retry limits, invalid state transitions, and non-positive lease durations.

- [ ] **Step 2: Run the focused tests to verify the expected red failure**

Run:

```powershell
python -m unittest tests.unit.test_database_migrations tests.unit.test_task_queue -v
```

Expected: import failure for `src.domain.task_queue` and migration assertions report the current version is 9.

- [ ] **Step 3: Implement migration 10 and immutable task value objects**

Append migration `task_queue` without changing versions 1-9. Define `TaskState`, `TaskRecord`, `TaskLease`, `TaskFailure`, and validation helpers in `src/domain/task_queue.py`; keep user payloads out of `TaskRecord` serialization and enforce a 16 KiB encrypted payload limit at the repository boundary.

- [ ] **Step 4: Run the focused tests to verify green**

Run the same command and expect all migration/domain tests to pass.

- [ ] **Step 5: Commit the schema/domain slice**

```powershell
git add src/services/database.py src/domain/task_queue.py tests/unit/test_database_migrations.py tests/unit/test_task_queue.py
git commit -m "feat: add durable task queue schema contract"
```

### Task 2: Implement encrypted queue persistence and atomic claims

**Files:**
- Create: `src/services/task_queue.py`
- Modify: `tests/unit/test_task_queue.py`

- [ ] **Step 1: Extend tests for enqueue, owner isolation, claim, lease expiry, renewal, completion, retry, and cancellation**

Cover two owners, duplicate task IDs, one active claim per task, an expired lease becoming claimable by another worker, worker-mismatched updates being rejected, retry attempts stopping at `max_attempts`, and payload plaintext not appearing in the database file.

- [ ] **Step 2: Run the queue tests and confirm the new repository methods fail**

```powershell
python -m unittest tests.unit.test_task_queue -v
```

Expected: missing `TaskQueue` import or missing method failures.

- [ ] **Step 3: Implement `TaskQueue` against `MetadataStore`**

Use the existing metadata connection boundary and the `past-partner/task/v1/<owner>/<id>` AAD. SQLite claims use an immediate transaction; PostgreSQL claims use a normal transaction with `FOR UPDATE SKIP LOCKED`. Store only a redacted failure code, never exception text. `claim()` must atomically set `leased`, increment attempts, and return the decrypted payload; `renew`, `complete`, `fail`, and `cancel` must check owner and lease owner in the same transaction.

- [ ] **Step 4: Run queue tests and verify green**

```powershell
python -m unittest tests.unit.test_task_queue -v
```

- [ ] **Step 5: Commit persistence**

```powershell
git add src/services/task_queue.py tests/unit/test_task_queue.py
git commit -m "feat: persist and lease owner-scoped tasks"
```

### Task 3: Add a bounded worker runner and application wiring

**Files:**
- Create: `src/services/task_worker.py`
- Modify: `src/server/application.py`
- Modify: `tests/unit/test_application_wiring.py`
- Create: `tests/unit/test_task_worker.py`

- [ ] **Step 1: Write failing worker and wiring tests**

Assert that `run_once()` claims and completes a registered handler, maps `RetryableTaskError` to a queued retry, maps unknown handlers and unexpected exceptions to terminal `failed` without leaking messages, and returns `False` when no task is available. Assert `Application.from_config()` exposes one queue sharing the application metadata store.

- [ ] **Step 2: Run the tests to verify red**

```powershell
python -m unittest tests.unit.test_task_worker tests.unit.test_application_wiring -v
```

Expected: missing worker module or missing `Application.task_queue`.

- [ ] **Step 3: Implement `TaskWorker` and wire the shared queue**

`TaskWorker` receives a handler mapping, worker ID, clock, lease duration, and max poll interval. It must catch only queue/domain errors for stable outcomes, redact all handler exceptions to `task_failed`, and provide `run_once()` plus a stop-event based `run_forever()`. `Application.from_config()` constructs `TaskQueue(metadata_store, encryption)` once and passes it into `Application`; `Application.close()` must not close the shared metadata store twice.

- [ ] **Step 4: Run worker and wiring tests to verify green**

```powershell
python -m unittest tests.unit.test_task_worker tests.unit.test_application_wiring -v
```

- [ ] **Step 5: Commit the worker slice**

```powershell
git add src/services/task_worker.py src/server/application.py tests/unit/test_task_worker.py tests/unit/test_application_wiring.py
git commit -m "feat: add bounded task worker runner"
```

### Task 4: Document the boundary and verify both metadata backends

**Files:**
- Modify: `README.md`
- Modify: `docs/privacy_policy.md`
- Modify: `.env.example`
- Create: `tests/integration/test_task_queue_backends.py`

- [ ] **Step 1: Write backend contract tests**

Run the same queue lifecycle against SQLite and a fake PostgreSQL metadata connection implementing the existing connection contract; skip only the real PostgreSQL case when `PAST_PARTNER_METADATA_DSN` is absent. Assert no payload, owner ID, or lease token appears in logs/errors.

- [ ] **Step 2: Run the backend tests and verify the intended red state**

```powershell
python -m unittest tests.integration.test_task_queue_backends -v
```

- [ ] **Step 3: Document configuration and operational limits**

Document that P4-06 provides a durable queue/worker port, uses the configured metadata backend, does not start a worker automatically, and does not claim distributed scheduling until a deployment supplies worker processes. Do not add broker credentials or pretend that a local worker is a multi-node guarantee.

- [ ] **Step 4: Run focused, full, and static verification**

```powershell
python -m unittest tests.unit.test_database_migrations tests.unit.test_task_queue tests.unit.test_task_worker tests.unit.test_application_wiring tests.integration.test_task_queue_backends -v
python -m unittest discover -s tests -p "test*.py" -q
python -m py_compile src/domain/task_queue.py src/services/task_queue.py src/services/task_worker.py src/server/application.py
git diff --check
```

The full suite must pass; optional provider/database integration tests may be skipped only when their explicit environment is absent.

- [ ] **Step 5: Commit documentation and verification record**

```powershell
git add README.md docs/privacy_policy.md .env.example tests/integration/test_task_queue_backends.py
git commit -m "docs: define p4-06 worker deployment boundary"
```

## Self-review

- The design covers the Phase 5 distributed-worker requirement without introducing a broker or silently claiming multi-node execution.
- All task payloads are encrypted and owner-scoped; routing fields are bounded and non-sensitive.
- Claim, lease expiry, renewal, completion, retry, and cancellation have deterministic tests.
- SQLite and PostgreSQL use backend-safe claim transactions.
- Existing import/training APIs remain unchanged; later tasks can enqueue those operations through the stable queue port.
