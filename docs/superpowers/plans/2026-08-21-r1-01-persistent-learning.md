# R1-01 Persistent Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist provider-independent style profiles, reviewed long-term memories, and the deterministic sparse vector index with owner/persona isolation and restart-safe APIs.

**Architecture:** Add one migration with three encrypted owner/persona-scoped tables. `LearningRepository` owns serialization, authenticated-encryption AAD, upsert/list/review/delete semantics, and persists a token-only vector index; `LearningService` validates persona ownership before calling it. The existing deterministic `VectorMemoryRetriever` consumes the persisted token index while retaining its privacy, recency, speaker-scope, and token-budget rules.

**Tech Stack:** Python 3.14, SQLite/PostgreSQL metadata-store protocol, authenticated encryption, `unittest`, existing HTTP server.

---

### Task 1: Define the migration and repository contracts with failing tests

**Files:**
- Modify: `src/services/database.py`
- Modify: `tests/unit/test_database_migrations.py`
- Create: `tests/unit/test_learning_repository.py`

- [ ] **Step 1: Write migration and persistence tests first.**

  Add migration 14 named `learning_repositories` with tables `style_profiles`, `long_term_memories`, and `vector_indexes`; assert version 14, idempotence, upgrade from v13, owner/persona indexes, and cascade-compatible foreign keys. Add repository tests for profile restart round-trip, memory review round-trip, owner isolation, encrypted-at-rest payloads, and deletion counts. The tests should import the not-yet-created `LearningRepository` and fail with `ModuleNotFoundError` or missing schema.

- [ ] **Step 2: Run the focused tests to verify the expected red state.**

  Run from the worktree:

  ```powershell
  $env:TEMP='D:\AI开发\.test-runtime'; $env:TMP=$env:TEMP
  python -m unittest tests.unit.test_database_migrations tests.unit.test_learning_repository -v
  ```

  Expected: failure because schema version 14 and the repository contract do not exist.

- [ ] **Step 3: Implement only the migration and repository skeleton needed by the tests.**

  Use encrypted `record_version=1` payloads and queryable routing metadata only:

  ```sql
  CREATE TABLE style_profiles (
      id TEXT PRIMARY KEY,
      owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
      persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
      record_version INTEGER NOT NULL CHECK (record_version = 1),
      encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0),
      updated_at TEXT NOT NULL,
      UNIQUE(owner_id, persona_id)
  );
  ```

  Create equivalent `long_term_memories` and `vector_indexes` tables. The latter stores encrypted JSON containing only `{index_version, entries:[{memory_id,tokens}]}`. Never place profile text, memory text, source excerpts, or vector tokens in plaintext columns.

- [ ] **Step 4: Run the focused tests and commit the green persistence contract.**

  Run the command in Step 2; expected: all migration and repository tests pass. Commit with `git add src/services/database.py tests/unit/test_database_migrations.py tests/unit/test_learning_repository.py src/services/learning_repository.py` and `git commit -m "feat: add persistent learning metadata schema"`.

### Task 2: Make the deterministic vector retriever consume a durable index

**Files:**
- Modify: `src/learning/vector_retrieval.py`
- Modify: `tests/unit/test_vector_retrieval.py`
- Modify: `src/services/learning_repository.py`

- [ ] **Step 1: Add failing index contract tests.**

  Test `VectorMemoryRetriever.build_index(memory)` returns bounded token tuples keyed by stable `memory_id`; test `retrieve(..., token_index=index)` produces the same ranking as the current calculation; test a missing candidate entry raises `VectorRetrievalError("invalid_vector_index", ...)` instead of silently rebuilding an untrusted index.

- [ ] **Step 2: Run `python -m unittest tests.unit.test_vector_retrieval -v` and verify the new tests fail.**

- [ ] **Step 3: Implement the minimal index API.**

  Keep `_tokens` as the single tokenizer. Add `build_index` and an optional `token_index` argument to `retrieve`; validate each indexed value as a bounded iterable of strings and use it for candidate scoring. Existing callers without an index continue using the current deterministic tokenizer.

- [ ] **Step 4: Run vector and repository tests, then commit.**

  Expected: existing and new vector tests pass; commit with `git commit -m "feat: persist deterministic learning vector index"`.

### Task 3: Add restart-safe learning service and application wiring

**Files:**
- Create: `src/services/learning_service.py`
- Modify: `src/server/application.py`
- Modify: `tests/unit/test_learning_service.py`
- Modify: `tests/unit/test_application_wiring.py`

- [ ] **Step 1: Write failing service tests.**

  Cover persona ownership validation, `save_style_profile`, `save_memory`, immutable review transitions, retrieval through the persisted index, and `delete_for_persona`. A second repository instance using the same metadata store must read the same data after the first service is discarded.

- [ ] **Step 2: Run the tests and verify the missing service/wiring failure.**

- [ ] **Step 3: Implement `LearningService`.**

  Validate the persona through `PersonaService.get(owner_id, persona_id)` before every read/write. Expose `get_style_profile`, `save_style_profile`, `get_memory`, `save_memory`, `review_memory`, `retrieve`, and `delete_for_persona`. Map missing records to a stable `LearningServiceError("learning_not_found", ...)` and never return another owner’s record.

- [ ] **Step 4: Wire `LearningRepository` and `LearningService` into `Application.from_config` and `Application.delete_persona`.**

  Include deletion counts under `deleted_learning` and preserve existing application constructor compatibility by making the new dependency explicit only at the single construction site. Run focused service/application tests and commit.

### Task 4: Add persona-scoped HTTP endpoints

**Files:**
- Modify: `src/server/http.py`
- Modify: `src/server/application.py`
- Create: `tests/integration/test_learning_api.py`

- [ ] **Step 1: Write failing HTTP tests.**

  Add these authenticated routes:

  - `PUT /api/v1/personas/{persona_id}/learning/style-profile` body `{"profile": {...}}`
  - `GET /api/v1/personas/{persona_id}/learning/style-profile`
  - `PUT /api/v1/personas/{persona_id}/learning/memory` body `{"memory": {...}}`
  - `GET /api/v1/personas/{persona_id}/learning/memory`
  - `PATCH /api/v1/personas/{persona_id}/learning/memory/{memory_id}` body `{"review_state":"accepted|rejected|needs_review"}`
  - `POST /api/v1/personas/{persona_id}/learning/retrieve` body `{"query":...,"max_candidates":...,"max_tokens":...,"max_age_days":...,"allowed_speaker_scopes":[...]}`

  Test restart persistence, accepted-only retrieval, owner/persona isolation, stable 404/400/422 errors, and that deletion makes all learning routes return 404. Do not assert raw database contents through the API.

- [ ] **Step 2: Run `python -m unittest tests.integration.test_learning_api -v` and confirm red.**

- [ ] **Step 3: Implement route matching, application methods, and safe error mapping.**

  Add route templates to `_route_template` and keep all learning routes under existing owner authentication and scope checks. Use `PUT` for idempotent aggregate replacement, `PATCH` for one review-state transition, and `POST` only for retrieval because it accepts a bounded query body.

- [ ] **Step 4: Run the focused HTTP tests and commit.**

### Task 5: Documentation, regression, and delivery verification

**Files:**
- Modify: `docs/ROADMAP.md`
- Modify: `README.md`
- Modify: `docs/privacy_policy.md` if current wording omits persistent learning boundaries
- Create/modify: `tests/integration/test_learning_api.py`, `tests/unit/test_learning_repository.py`

- [ ] **Step 1: Update the roadmap.**

  Mark R0-04 as merged and remove the stale “before merge” wording. Document R1-01 routes, encrypted storage, accepted-memory-only retrieval, and the fact that embeddings/model calls are not included.

- [ ] **Step 2: Run all verification commands.**

  ```powershell
  $env:TEMP='D:\AI开发\.test-runtime'; $env:TMP=$env:TEMP
  $env:PYTHONPYCACHEPREFIX='D:\AI开发\.test-runtime\pycache-r1-01'
  python -m unittest discover -s tests -p 'test*.py' -v
  node --test tests/web_workspace_test.mjs
  python -m compileall -q src tests
  git diff --check
  codegraph sync
  codegraph status
  ```

  Expected: all Python/Node tests pass, compilation and diff checks are clean, and CodeGraph reports `Index is up to date`.

- [ ] **Step 3: Commit the complete R1-01 branch and provide it for user acceptance.**

  Use `git status --short --branch` to confirm only owned files are staged, then commit with `git commit -m "feat: persist persona learning state"`. Do not merge or push until the user accepts the branch.

**Spec coverage review:** This plan covers the R1-01 roadmap requirements: restart recovery, owner/persona partitioning, encrypted profile/memory/vector state, accepted-memory-only retrieval, review state persistence, persona deletion cascade, and persona-scoped APIs. It deliberately excludes R1-02 full export/retention, R1-03 accounts/OIDC, R1-04 external workers, and R2 real embedding/media capabilities.
