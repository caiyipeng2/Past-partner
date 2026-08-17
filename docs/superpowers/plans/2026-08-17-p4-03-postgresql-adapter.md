# P4-03 PostgreSQL Adapter Implementation Plan

**Goal:** 在不改变默认 SQLite 和加密业务契约的前提下，完成可真实连接 PostgreSQL 的 MetadataStore 适配器、迁移账本、连接池和启动生命周期。

**Branch:** `codex/p4-03-postgresql`

**Prerequisite:** 用户确认本设计；真实服务验收需要可访问的 PostgreSQL DSN。当前环境未安装 Docker/`psql`，实现前需提供受控 PostgreSQL 服务或明确只做 adapter/合同测试。

## Task 1: Extend the metadata port and stable errors

### Step 1: Add red tests

Create `tests/unit/test_metadata_store.py` cases for:

- `close()` is part of the lifecycle and idempotent;
- `MetadataIntegrityError` is stable and never includes driver text;
- malformed adapters cannot bypass the connection contract;
- SQLite transaction and error behavior remains unchanged.

### Step 2: Implement the port changes

Modify:

- `src/services/metadata_store.py`
- `src/services/sqlite_metadata_store.py`

Add `close`, stable integrity/operational errors and SQLite mapping without exposing `sqlite3` types through the protocol.

### Step 3: Verify

```powershell
python -m unittest tests.unit.test_metadata_store -v
```

Commit: `feat: extend metadata store lifecycle contract`

## Task 2: Add PostgreSQL dependency and adapter primitives

### Step 1: Add red adapter tests

Create `tests/unit/test_postgresql_metadata_store.py` using fake psycopg/pool objects. Cover:

- DSN is never present in stable errors;
- qmark parameters and memoryview normalization;
- `BEGIN IMMEDIATE` maps only inside the adapter;
- pool checkout/return and close are balanced;
- connection and integrity exceptions map to stable codes;
- missing optional driver fails closed.

### Step 2: Implement adapter

Create `src/services/postgresql_metadata_store.py` and add the optional `psycopg[binary,pool]` dependency to the production dependency group. Keep imports lazy so SQLite-only installs still start when `metadata_backend=sqlite`.

### Step 3: Verify and commit

```powershell
python -m unittest tests.unit.test_postgresql_metadata_store tests.unit.test_metadata_store -v
```

Commit: `feat: add postgresql metadata store adapter`

## Task 3: Add PostgreSQL migration ledger

### Step 1: Add migration tests

Create `tests/unit/test_postgresql_migrations.py` with a fake connection and a real PostgreSQL integration test gated by `PAST_PARTNER_METADATA_DSN`. Verify versions 1-9, `BYTEA`, idempotence, checksum mismatch and rollback.

### Step 2: Implement migration runner

Create `src/services/postgresql_database.py` or a narrowly scoped migration module. Reuse logical migration names/checksums from `src/services/database.py`, compile only PostgreSQL DDL, and never alter SQLite migrations.

### Step 3: Verify and commit

```powershell
python -m unittest tests.unit.test_postgresql_migrations -v
```

Commit: `feat: add postgresql metadata migrations`

## Task 4: Make repository errors and application lifecycle driver-neutral

### Step 1: Add regression tests

Extend repository tests with fake PostgreSQL connection errors and add wiring coverage for:

- backend/DSN/pool config;
- shared PostgreSQL store instance;
- application close returning the pool;
- unsupported/partial configuration failing before any write.

### Step 2: Update implementation

Modify:

- `src/server/config.py`
- `src/server/application.py`
- `src/server/__main__.py`
- `src/services/persona_repository.py`
- `src/services/import_repository.py`
- `src/services/consent_repository.py`
- `src/services/training_repository.py`
- `src/services/conversation_repository.py`
- `src/services/local_auth.py`

Repositories catch stable metadata integrity errors instead of importing a concrete driver exception. `Application.from_config` selects the backend only after validation, and `Application.close()`/server shutdown closes the shared store.

### Step 3: Verify and commit

```powershell
python -m unittest tests.unit.test_metadata_wiring tests.unit.test_server_config tests.unit.test_persona_repository tests.unit.test_import_repository tests.unit.test_consent_service tests.unit.test_training_repository tests.unit.test_conversation_repository tests.unit.test_local_auth -v
```

Commit: `feat: wire postgresql metadata backend`

## Task 5: Real PostgreSQL integration verification

Set a temporary controlled `PAST_PARTNER_METADATA_DSN` and run:

```powershell
python -m unittest tests.integration.test_postgresql_metadata_store -v
```

The integration suite must create an isolated database/schema, run migrations, instantiate `Application.from_config`, exercise owner-scoped persona/import/consent/training/conversation paths, then close the pool and clean the schema. Credentials are supplied only through the environment and never committed.

If no DSN is available, report this task as blocked for real-service verification rather than substituting SQLite.

## Task 6: Repository-wide verification and acceptance

From `D:\AI开发\.worktrees\p4-03-postgresql` run:

```powershell
python -m unittest discover -s tests -p "test*.py" -q
node --test tests/web_workspace_test.mjs
dart analyze --format=machine
flutter test
git diff --check
codegraph sync
```

Report default SQLite behavior, explicit PostgreSQL configuration, exact real-DSN evidence, schema/crypto compatibility, test results and any pre-existing flaky tests. Do not merge, push or delete the branch until user acceptance.
