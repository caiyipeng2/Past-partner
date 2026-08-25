# R1-04 Queue Broker Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a vendor-neutral, reliable notification boundary between the encrypted durable task queue and a future production broker.

**Architecture:** Every task enqueue records one redacted notification in the same metadata transaction as the encrypted task row. A publisher drains the outbox with stable retry metadata and an idempotent message ID. The only broker implementation in this slice is an in-memory broker for deterministic tests; Redis, RabbitMQ, credentials, and production broker selection remain future work.

**Tech Stack:** Python 3.11+, SQLite/PostgreSQL-compatible SQL, existing `MetadataStore` and AES-GCM queue, `unittest`.

## Task 1: Define the outbox migration and broker message contract

**Files:**
- Modify: `src/services/database.py`
- Create: `src/domain/task_broker.py`
- Modify: `tests/unit/test_database_migrations.py`
- Create: `tests/unit/test_task_broker.py`

- [ ] Add migration 17 for a task notification outbox with task foreign-key cascade, pending index, bounded retry state, and no payload/owner columns.
- [ ] Add immutable `TaskNotification` and `BrokerDelivery` values with bounded identifiers and stable validation errors.
- [ ] Add red tests for migration history, same-transaction enqueue staging, secret exclusion, duplicate message IDs, and ack/nack ownership.

## Task 2: Implement durable outbox publishing and deterministic broker

**Files:**
- Modify: `src/services/task_queue.py`
- Create: `src/services/task_broker.py`
- Modify: `tests/unit/test_task_broker.py`

- [ ] Stage one notification during `TaskQueue.enqueue` without changing task payload semantics.
- [ ] Add bounded list/mark/defer outbox operations with stable error codes and retry backoff.
- [ ] Add `TaskBrokerPublisher.publish_once()` with idempotent publish-before-mark ordering and redacted failure handling.
- [ ] Add a thread-safe `InMemoryTaskBroker` that supports dedupe, receive, consumer-bound ack, nack requeue, and visibility recovery for tests only.

## Task 3: Document the operational boundary

**Files:**
- Modify: `README.md`
- Modify: `docs/privacy_policy.md`
- Modify: `docs/ROADMAP.md`
- Create: `tests/integration/test_broker_contract.py`

- [ ] Document the durable outbox guarantee and explicit future boundary for production broker adapters and credentials.
- [ ] Assert documentation does not claim Redis/RabbitMQ/SIEM or production broker support.

## Task 4: Verify and hand off

- [ ] Run focused broker, migration, queue, and documentation tests.
- [ ] Run `npm test`, `compileall`, `git diff --check`, and CodeGraph status.
- [ ] Commit the branch and provide it for user acceptance; do not merge or push before confirmation.

## Acceptance boundary

This slice is accepted when task creation and notification staging are atomic, publishing is retryable and idempotent, broker delivery ownership and redelivery are deterministic, no payload/owner/secret enters broker messages or errors, and documentation clearly excludes a production broker adapter. It is not acceptance of Redis/RabbitMQ deployment, cross-process metrics, alerting, tracing, log shipping, or SIEM.
