# R1-04 External Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a separate worker launch surface that processes the existing encrypted task queue with bounded, testable lease execution without claiming broker, alerting, or SIEM support before those slices exist.

**Architecture:** A small `src.worker` package owns validated worker settings, bounded runner lifecycle, and test-only probe handlers. The worker process builds the same `ServerConfig` and `Application` as the API process, then reuses `TaskQueue`/`TaskWorker` against the configured metadata backend. Production has no implicit business handlers; unknown task types fail with the existing stable code instead of silently succeeding.

**Tech Stack:** Python 3.11+, `unittest`, existing SQLite/PostgreSQL `MetadataStore`, AES-GCM task envelopes, setuptools console entry points.

---

### Task 1: Define worker settings and runner contract

**Files:**
- Create: `src/worker/__init__.py`
- Test: `tests/unit/test_worker_runtime.py`

- [ ] **Step 1: Write failing tests** for worker ID/lease/poll/max-task validation, one-shot idle behavior, bounded processing, and stable statistics.
- [ ] **Step 2: Run the focused test module** and confirm imports/runner behavior fail before implementation.
- [ ] **Step 3: Implement `WorkerSettings`, `WorkerStats`, and `WorkerRunner`** with bounded identifiers, `run_once`, `run_until_idle`, and cooperative `run_forever` methods that delegate all claim/lease/failure semantics to `TaskWorker`.
- [ ] **Step 4: Run the focused unit tests** and confirm all worker runtime assertions pass.
- [ ] **Step 5: Commit** the runtime contract and tests.

### Task 2: Add the external worker launch surface

**Files:**
- Create: `src/worker/__main__.py`
- Modify: `pyproject.toml`
- Test: `tests/integration/test_worker_launch.py`

- [ ] **Step 1: Write failing integration tests** that enqueue a test-only probe task, launch `python -m src.worker --once`, verify the encrypted result is persisted without the secret payload, and verify an idle worker exits cleanly without starting HTTP.
- [ ] **Step 2: Run the focused integration tests** and confirm the module/entry point is missing.
- [ ] **Step 3: Implement CLI parsing and process lifecycle**: reuse `ServerConfig.from_env`, support `--worker-id`, `--once`, `--max-tasks`, `--lease-seconds`, and `--poll-seconds`, install SIGINT/SIGTERM stop events, close `Application` exactly once, and log only bounded worker ID, counts, and stable status.
- [ ] **Step 4: Add a test-only `worker.probe` handler** that returns metadata only and never echoes payload values; production starts with no implicit handlers.
- [ ] **Step 5: Add the `companion-worker` setuptools entry point** and rerun focused integration tests.
- [ ] **Step 6: Commit** the external worker launch surface.

### Task 3: Document the operational boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/privacy_policy.md`
- Modify: `docs/ROADMAP.md`
- Test: `tests/integration/test_dependency_groups.py` or a focused worker contract test if documentation assertions are needed.

- [ ] **Step 1: Update documentation** with the worker command, required shared metadata/master-key configuration, bounded one-shot verification command, and explicit statement that broker, cross-process metrics, alerts, log shipping, and SIEM remain future R1-04 slices.
- [ ] **Step 2: Add/adjust documentation contract assertions** so the worker command and non-claims cannot drift.
- [ ] **Step 3: Run focused tests and `git diff --check`, then commit** the documentation boundary.

### Task 4: Full verification and handoff

**Files:**
- No source changes expected.

- [ ] **Step 1: Run worker-focused unit and integration tests.**
- [ ] **Step 2: Run `npm test` on the feature branch.**
- [ ] **Step 3: Run `codegraph sync` and verify clean tracked state.**
- [ ] **Step 4: Report the branch, commits, tests, and black-box acceptance criteria without merging or pushing before user confirmation.**
