# P4-01 Storage Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Each task is independently testable and must be completed before the next task begins.

**Goal:** 在不改变现有 SQLite、AES-GCM、HTTP、Flutter 和本地对象布局的前提下，为上传对象建立可替换的 `BlobStore` 协议、默认本地适配器、运行时后端选择和稳定错误契约。

**Architecture:** 新增 `src/services/blob_store.py`，定义只接受逻辑 key 的 `BlobStore`、`BlobReceipt` 和 `StorageError` 层级；`LocalBlobStore` 复用 `StorageLayout` 完成 key 校验、临时文件、fsync、原子替换、有界读取和幂等删除。`UploadService` 增加可选 `blob_store`，缺省时由 `StorageLayout` 创建本地适配器；`Application.from_config` 根据 `ServerConfig.storage_backend` 装配唯一已实现的 `local` 后端。P4-01 不加入云 SDK，不迁移元数据，不改变密文格式或 HTTP 契约。

**Tech Stack:** Python 3、`unittest`、现有 `StorageLayout`/`UploadService`、SQLite 元数据仓储、PowerShell 验证命令。

---

## Task 1: Define the BlobStore contract and stable storage errors

**Files:**
- Create `src/services/blob_store.py`
- Create `tests/unit/test_blob_store.py`

### Step 1: Write the failing contract tests

Add `unittest.TestCase` coverage for:

1. `BlobReceipt` contains only `key`, confirmed `length`, and lowercase `sha256`.
2. `StorageError` exposes a stable `code` and never formats a filesystem path, temporary name, secret, or source body into its public message.
3. `BlobStore` is a runtime-checkable protocol with exactly `put`, `iter_bytes`, `exists`, and `delete` call shapes.
4. A fake implementation can satisfy the protocol without importing `StorageLayout`.

Run:

```powershell
python -m unittest tests.unit.test_blob_store -v
```

Expected result before implementation: the module import or protocol symbols are missing.

### Step 2: Implement the contract

In `src/services/blob_store.py`:

- Define `BlobReceipt` as an immutable slots dataclass.
- Define `StorageError(ValueError)` with `code` and a safe, caller-provided message.
- Add specific errors for `invalid_key`, `object_not_found`, `object_conflict`, `storage_read_failed`, `storage_write_failed`, and `storage_backend_unsupported`.
- Define the `BlobStore(Protocol)` signatures from the approved design, using `BinaryIO`, `Iterator[bytes]`, and keyword-only `length`, `sha256`, and `block_bytes`.
- Keep all path and storage-layout knowledge out of the protocol.

### Step 3: Verify

```powershell
python -m unittest tests.unit.test_blob_store -v
git diff --check
```

Expected result: all contract-shape tests pass and no error message contains a path-like or secret value.

### Step 4: Commit

```powershell
git add src/services/blob_store.py tests/unit/test_blob_store.py
git commit -m "feat: define blob storage contract"
```

## Task 2: Implement the LocalBlobStore adapter

**Files:**
- Modify `src/services/blob_store.py`
- Modify `tests/unit/test_blob_store.py`

### Step 1: Add failing local-adapter tests

Use a temporary directory and the existing `StorageLayout` to cover:

- valid logical keys write/read/exists/delete;
- rejection of empty keys, leading `/` or `\\`, drive letters, absolute POSIX/Windows paths, NUL, `..` segments, and keys resolving outside the configured root;
- declared length and SHA-256 mismatch leaves no committed target;
- duplicate `put` reports `object_conflict` and does not overwrite the old object;
- injected source read failure maps to `storage_read_failed`;
- injected write/replace failure maps to `storage_write_failed` and removes every temporary file;
- an existing old object remains readable if a replacement fails before atomic commit;
- `iter_bytes` returns bounded blocks and never calls `read()` without a positive size;
- missing `delete` returns `False`, existing `delete` returns `True`, and repeated delete stays idempotent;
- adapter errors do not include the absolute root, temporary filename, or source bytes.

Run the focused test file and confirm the new tests fail before implementation.

### Step 2: Implement LocalBlobStore

Implement `LocalBlobStore(layout: StorageLayout)` in `src/services/blob_store.py`:

- Normalize and validate a non-empty UTF-8 relative logical key. Permit `/` only as a separator between validated non-empty segments; reject leading separators, backslashes, drive syntax, NUL, `.`/`..`, and any candidate outside `layout.root`.
- Map a logical key to a `StorageLayout`-contained path without exposing that path through `BlobReceipt` or public errors.
- `put` validates the declared length and strict 64-character SHA-256, creates a random sibling `.tmp`, reads the source in a fixed bounded block, computes actual length/digest, fsyncs, and uses `os.replace` only after all checks pass.
- Refuse an already existing destination with `object_conflict`; never silently overwrite it.
- Always unlink the temporary file in `finally`; translate `OSError` and source failures to stable storage errors without chaining path text into the client message.
- `iter_bytes` opens read-only and yields at most the requested block size; missing objects produce `object_not_found`.
- `exists` returns `False` for a missing object and does not expose path errors.
- `delete` is idempotent and maps unexpected filesystem failures to `storage_write_failed`.

### Step 3: Verify

```powershell
python -m unittest tests.unit.test_blob_store -v
```

Expected result: the full local adapter contract passes, including cleanup and bounded-read cases.

### Step 4: Commit

```powershell
git add src/services/blob_store.py tests/unit/test_blob_store.py
git commit -m "feat: add atomic local blob store"
```

## Task 3: Add storage-backend configuration and explicit unsupported-backend failure

**Files:**
- Modify `src/server/config.py`
- Modify `tests/unit/test_server_config.py`
- Modify `.env.example`
- Modify `README.md`

### Step 1: Write failing configuration tests

Add tests that assert:

- `ServerConfig()` and `ServerConfig.from_env()` default to `storage_backend == "local"`.
- `PAST_PARTNER_STORAGE_BACKEND=local` is accepted and normalized consistently.
- `s3`, `minio`, `postgres`, an empty explicit value, and an arbitrary value fail validation with stable `storage_backend_unsupported` information.
- a configuration error does not echo a token, provider key, full data path, or request body.

Run:

```powershell
python -m unittest tests.unit.test_server_config -v
```

Expected result before implementation: `ServerConfig` has no storage-backend field or validation.

### Step 2: Implement configuration

In `ServerConfig`:

- Add `storage_backend: str = "local"`.
- Parse `PAST_PARTNER_STORAGE_BACKEND` in `from_env`.
- Validate only the registered `local` backend. Raise a configuration error carrying code `storage_backend_unsupported`; do not silently fall back.
- Keep the existing pairing, retention, size, and secret-redaction validation unchanged.

Document in `.env.example` and `README.md` that `local` is the only P4-01 backend, that unknown values fail startup, and that S3/MinIO/PostgreSQL/KMS are later tasks rather than accepted settings. Do not add cloud credentials or SDK dependencies.

### Step 3: Verify

```powershell
python -m unittest tests.unit.test_server_config -v
git diff --check
```

### Step 4: Commit

```powershell
git add src/server/config.py tests/unit/test_server_config.py .env.example README.md
git commit -m "feat: configure storage backend explicitly"
```

## Task 4: Inject BlobStore into UploadService without changing legacy callers

**Files:**
- Modify `src/services/upload_service.py`
- Modify `tests/unit/test_upload_service.py`
- Modify `tests/integration/test_http_api.py` only where an existing filesystem assertion must use the adapter contract

### Step 1: Add a spy-store regression test

Add a small in-memory/test `BlobStore` that records logical keys and fails if the service asks for an absolute path. Construct `UploadService(..., blob_store=spy)` and assert the service accepts it while the existing constructor without `blob_store` still works.

Run the focused upload tests and confirm the injected-store assertions fail before migration.

### Step 2: Add constructor compatibility

- Extend `UploadService.__init__` with `blob_store: BlobStore | None = None` after existing optional dependencies so positional legacy callers remain valid.
- Preserve `self.storage` for metadata/layout compatibility used by training and migration helpers.
- When `blob_store` is omitted, construct `LocalBlobStore(storage)`.
- Keep `max_chunk_bytes`, `read_block_bytes`, parser, media inspector, locks, and cleanup behavior unchanged.

### Step 3: Migrate chunk and completed-payload paths

Replace direct object-file operations in `put_chunk`, `complete`, `cancel`, `_delete_import_locked`, and `payload_path` with logical keys and BlobStore calls:

- Define private key helpers for `upload-parts/<import_id>-<index>.part` and `payloads/<import_id>.bin`; helpers return logical keys, not `Path` objects.
- Preserve encryption, digest, contiguous-index checks, manifest writes, metadata rollback, and existing `UploadError` codes.
- For a chunk, encrypt into a bounded source and call `blob_store.put`; do not make the adapter receive a client path.
- For completion, stream the encrypted chunk content into a temporary source accepted by `put`; maintain final sentinel handling and payload digest checks.
- Translate `StorageError` to the existing service-level `UploadError` codes (`chunk_missing`, `chunk_corrupt`, `payload_unavailable`, `deletion_failed`, or the existing metadata errors) without exposing adapter messages.
- `cancel` and deletion use idempotent `delete`; metadata remains authoritative and is still committed under the existing locks.

Update tests that inspect `_chunk_path`/`payload_path` so they either query `LocalBlobStore` through a test-only helper or assert `exists(key)`; no production API returns an absolute path.

### Step 4: Verify chunk/payload behavior

```powershell
python -m unittest tests.unit.test_upload_service tests.integration.test_large_upload_contract -v
```

Expected result: existing chunk, duplicate, conflict, digest, completion, cancellation, deletion, and bounded-payload tests pass with the injected spy proving no direct absolute-path dependency.

## Task 5: Migrate preview temporary objects through the boundary

**Files:**
- Modify `src/services/upload_service.py`
- Modify `tests/unit/test_upload_service.py`
- Modify `tests/integration/test_http_api.py`

### Step 1: Add failing preview boundary tests

Extend the spy store to observe `preview/<id>-<index>.bin` keys. Assert that:

- preview materialization, parser input cleanup, and preview errors leave no committed temporary object;
- the service never passes an absolute path to the BlobStore or includes a temporary filename in an `UploadError`.

Keep the parser interface unchanged: it may still receive a local seekable temporary file created by a narrowly scoped adapter bridge, but its lifecycle must be owned by the BlobStore integration and cleaned in `finally`.

### Step 2: Migrate preview lifecycle

- Introduce private helpers for creating a short-lived local materialization from a logical preview key only where the existing parser contract requires a seekable `Path`.
- Route preview object creation/removal through the BlobStore boundary; preserve bounded source reads, disk-capacity checks, startup stale cleanup, and existing parser error codes.
- Ensure cleanup is attempted on success, parser failure, and generator close.
- Keep `media-inspection` and `training-source` on their current `PlaintextLeaseRegistry`/disk-capacity guarded paths in this task. Add regression assertions that their existing cleanup and `media_inspection_*`/`training_dataset_*` error mappings remain unchanged; schedule any future migration as a separate storage-boundary task.

### Step 3: Verify

```powershell
python -m unittest tests.unit.test_upload_service tests.integration.test_http_api -v
```

Expected result: preview, media inspection, training-source, stale cleanup, and HTTP response contracts remain unchanged; preview temporary files are removed after each path.

## Task 6: Wire the configured adapter at application startup

**Files:**
- Modify `src/server/application.py`
- Create or modify `tests/unit/test_application_wiring.py`

### Step 1: Write failing wiring tests

Test `Application.from_config` with:

- default configuration and an isolated `data_dir`, asserting `uploads.blob_store` is a `LocalBlobStore` rooted at the configured data directory;
- explicit `storage_backend="local"`, asserting the same behavior;
- an unsupported backend, asserting startup fails before any upload object is written and exposes `storage_backend_unsupported`.

Use existing test-mode provider/database fixtures; do not require external cloud services.

### Step 2: Implement wiring

- Add a small factory in `src/services/blob_store.py` or `src/server/application.py` that maps `"local"` to `LocalBlobStore(StorageLayout(config.data_dir))` and rejects all other values.
- Pass the resulting adapter to `UploadService(..., blob_store=blob_store)`.
- Keep `StorageLayout` available for database paths, legacy JSON migration, retention, and other non-P4-01 responsibilities.
- Do not alter HTTP routes, request payloads, authentication, encryption, provider setup, or Flutter configuration.

### Step 3: Verify

```powershell
python -m unittest tests.unit.test_application_wiring tests.unit.test_server_config -v
```

Expected result: application startup selects only the explicit local adapter and rejects unknown backends before serving requests.

### Step 4: Commit

```powershell
git add src/services/blob_store.py src/server/application.py tests/unit/test_application_wiring.py
git commit -m "feat: wire blob store into application"
```

## Task 7: Run the repository-wide verification suite

**Files:**
- No source changes expected; update tests only if a failure is a genuine P4-01 regression.

Run in this order from `D:\AI开发\.worktrees\p4-01-storage-boundary`:

```powershell
python -m unittest discover -s tests -p "test*.py" -v
node --test tests/web_workspace_test.mjs
dart analyze --format=machine
flutter test
git diff --check
```

Also run a targeted search to ensure no P4-01 upload path still bypasses the boundary:

```powershell
rg -n "destination\.open|destination\.unlink|payload_path\(.*\)\.open|_chunk_path\(.*\)\.open|storage\.object_path\(\"(upload-parts|payloads|preview|media-inspection)" src/services/upload_service.py
```

Expected result: the search has no production bypasses except explicitly documented adapter-owned materialization helpers; all Python, web, Dart, Flutter, and whitespace checks pass. Record any unavailable platform command rather than treating it as a pass.

Commit any narrowly scoped regression-test adjustment separately:

```powershell
git add tests
git commit -m "test: verify storage boundary integration"
```

## Task 8: Prepare user acceptance without merging

Before user acceptance, report:

- branch name `codex/p4-01-storage-boundary` and commit list;
- exact focused and full test commands/results;
- default local backend behavior and explicit unsupported-backend failure;
- confirmation that SQLite, AES-GCM, HTTP/Flutter contracts, and existing local object layout were not migrated;
- any unavailable Android/iOS or cloud-provider checks.

Do not merge, delete the worktree, or push `main` in this task. After the user explicitly accepts the implementation, follow the repository rule: merge into `main`, rerun the post-merge suite, push `origin/main`, and only then remove the merged temporary worktree/branch.
