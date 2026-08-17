# P4-02 Metadata Boundary Implementation Plan

**Goal:** 在保持现有加密 SQLite 行为的同时，为人物、导入、授权、训练、会话和认证元数据建立可替换的 `MetadataStore` 边界，为后续 PostgreSQL adapter 做好真实契约准备。

**Architecture:** 新增 `MetadataStore` 协议和 `SQLiteMetadataStore` adapter；所有 metadata repository 通过同一个注入的 store 获取连接和事务，`Application.from_config` 根据 `PAST_PARTNER_METADATA_BACKEND` 装配唯一已实现的 `sqlite` backend。P4-02 不连接 PostgreSQL，不迁移 schema，不改变加密 payload、HTTP、Flutter 或 BlobStore 契约。

**Tech Stack:** Python 3 标准库、`sqlite3`、现有 `SQLiteMigrator`/repository、`unittest`、Node Web 合同测试、Dart/Flutter 验证。

## Task 1: Define the MetadataStore contract and stable errors

### Step 1: Write failing contract tests

**Files:**

- Create `tests/unit/test_metadata_store.py`
- Modify only if needed: `tests/unit/test_database_migrations.py`

Cover:

- runtime-checkable store/connection protocols expose only the supported lifecycle;
- transaction commit and rollback are explicit;
- adapter errors have stable codes and do not reveal paths, DSNs, SQL or payloads;
- migration is idempotent and returns the applied schema version.

### Step 2: Implement the port

**Files:**

- Create `src/services/metadata_store.py`

Define the minimal protocols and error hierarchy. Keep concrete `sqlite3` types out of the protocol module.

### Step 3: Verify and commit

Run:

```powershell
python -m unittest tests.unit.test_metadata_store -v
```

Commit: `feat: define metadata store contract`

## Task 2: Add the SQLite adapter without changing schema behavior

### Step 1: Add adapter regression tests

**Files:**

- Extend `tests/unit/test_metadata_store.py`
- Add focused migration/foreign-key/error tests in `tests/unit/test_database_migrations.py`

Verify one adapter owns migration and connection setup, repeated migration is safe, write failures roll back, and the current migration history remains byte-for-byte compatible.

### Step 2: Implement the SQLite adapter

**Files:**

- Modify `src/services/metadata_store.py`
- Keep `src/services/database.py` as the migration implementation, moving only adapter-owned connection setup where required.

`SQLiteMetadataStore` must preserve the current database path and `PRAGMA foreign_keys = ON` behavior. No migration version changes are allowed in this task.

### Step 3: Verify and commit

Run:

```powershell
python -m unittest tests.unit.test_metadata_store tests.unit.test_database_migrations -v
```

Commit: `feat: add sqlite metadata store`

## Task 3: Inject one store through application and repositories

### Step 1: Add failing wiring tests

**Files:**

- Create `tests/unit/test_metadata_wiring.py`
- Extend existing repository construction tests only where needed.

Assert all repositories created by `Application.from_config` receive the same store instance, legacy constructors remain valid during the transition, and no repository silently creates a second store.

### Step 2: Implement repository injection

**Files:**

- `src/server/config.py`
- `src/server/application.py`
- `src/services/persona_repository.py`
- `src/services/import_repository.py`
- `src/services/consent_repository.py`
- `src/services/training_repository.py`
- `src/services/conversation_repository.py`
- Any small shared repository helper required by the port.

Add `PAST_PARTNER_METADATA_BACKEND` with default `sqlite`; reject `postgres`, `postgresql`, empty and unknown values before application writes. Keep old path-based construction as an explicit compatibility factory, not an implicit per-repository global.

### Step 3: Verify and commit

Run:

```powershell
python -m unittest tests.unit.test_metadata_store tests.unit.test_metadata_wiring tests.unit.test_database_migrations tests.unit.test_persona_repository tests.unit.test_import_repository tests.unit.test_consent_repository tests.unit.test_training_repository tests.unit.test_conversation_repository -v
```

Commit: `feat: wire metadata store through repositories`

## Task 4: Preserve application and API behavior

Run focused integration coverage for persona, import, consent, training, chat, privacy, retention and startup configuration. Confirm the backend selection change does not alter HTTP responses, encrypted records, owner scoping or Flutter contracts.

Commit any narrowly scoped test-only adjustment separately.

## Task 5: Repository-wide verification

From `D:\AI开发\.worktrees\p4-02-metadata-boundary` run:

```powershell
python -m unittest discover -s tests -p "test*.py" -q
node --test tests/web_workspace_test.mjs
dart analyze --format=machine
flutter test
git diff --check
```

Run CodeGraph sync and inspect for direct repository calls to `sqlite3.connect` or `SQLiteMigrator` outside the adapter/compatibility factory. No real PostgreSQL service is required or claimed in P4-02.

## Task 6: Prepare user acceptance without merging

Report:

- branch and commit list;
- exact focused and full test results;
- default `sqlite` backend and explicit unsupported-backend behavior;
- confirmation that schema, encrypted payloads, HTTP/Flutter, BlobStore and local layout are unchanged;
- explicit statement that PostgreSQL is a follow-up adapter task, not implemented or simulated here.

Do not merge, push, delete the branch or remove the worktree until the user explicitly accepts the implementation.
