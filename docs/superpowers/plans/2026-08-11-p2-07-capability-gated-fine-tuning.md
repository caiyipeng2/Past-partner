# P2-07 Capability-Gated Fine-Tuning Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add auditable, owner-scoped fine-tuning job APIs that submit only accepted persona-authored text to an adapter that explicitly supports fine tuning, and fail closed everywhere else.

**Architecture:** P2-07 does not call the legacy `TrainingPipeline` or `FineTuner`: they accept arbitrary local paths and intentionally have no production execution capability. A new `FineTuningService` will build a bounded-memory JSONL dataset from encrypted completed imports, validate an exact training consent and capability/price metadata, then submit it through an optional provider adapter contract. Job metadata is encrypted in SQLite; plaintext source and dataset files exist only in controlled temporary directories and are removed before a request returns. No production catalog model is newly advertised as capable until a real provider adapter is installed.

**Tech Stack:** Python 3 standard library, SQLite migrations, authenticated encryption, existing parser plugins, provider gateway, unittest, Node web-contract tests.

---

## Non-Negotiable Scope

- The dataset contains only records where `sender_role == "persona"` and `review_state == "accepted"`. User, other, unknown, rejected, and unreviewed messages never become target examples.
- The required consent values are `data_category="persona_text"`, `purpose="fine_tuning"`, and `authorization_scope="fine_tuning:<import_id>"`. This is separate from image/audio/video consent.
- A job reaches `completed` only after the adapter returns a non-empty provider artifact ID and a non-empty evaluation result. A successful HTTP response cannot be synthesized locally.
- Catalog capability, training price, configured adapter, import ownership, persona ownership, target coverage, sample count, and dataset digest are validated before any provider transfer.
- Test-mode deterministic provider responses prove the contract. Development and production never expose the test provider and never produce a fabricated training result.
- This task creates the gateway contract and durable job lifecycle, not a claim that every existing provider supports fine tuning. Unsupported or unconfigured providers return a stable explicit error.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/providers/base.py` | Fine-tuning request, submission, status, and optional adapter protocol. |
| `src/providers/catalog.py` | Training-token price metadata and model-level capability-aware cost estimate. |
| `src/providers/gateway.py` | One provider validation and error-translation boundary for submit/status/cancel. |
| `src/providers/testing.py` | Test-only adapter with a deterministic verified job lifecycle. |
| `src/domain/training_jobs.py` | Validated durable job state and immutable result metadata. |
| `src/services/database.py` | Migration 6 for encrypted owner/persona-scoped training job records. |
| `src/services/training_repository.py` | Authenticated encrypted persistence for job metadata only. |
| `src/services/training_dataset.py` | Streaming, target-role-only JSONL generation and digest calculation. |
| `src/services/training_service.py` | Preflight, consent gate, adapter invocation, status refresh, cancellation, and persona cleanup. |
| `src/services/upload_service.py` | Safe per-import iteration of corrected, mapped normalized records for training. |
| `src/server/application.py` | Dependency wiring, persona deletion integration, export metadata, and facade methods. |
| `src/server/http.py` | Versioned estimate, create, list, get, and cancel training-job routes and status mapping. |
| `tests/unit/test_training_*.py` | Domain, repository, dataset, service, and gateway behavior. |
| `tests/integration/test_http_api.py` | Owner-authenticated HTTP success and failure contracts. |
| `README.md`, `docs/privacy_policy.md` | Honest capability, dataset, consent, and retention disclosure. |

### Task 1: Define Provider Fine-Tuning Contracts And Cost Metadata

**Files:**
- Modify: `src/providers/base.py`
- Modify: `src/providers/catalog.py`
- Modify: `src/providers/gateway.py`
- Modify: `src/providers/testing.py`
- Modify: `tests/unit/test_provider_catalog.py`
- Modify: `tests/unit/test_provider_gateway.py`

- [x] **Step 1: Write failing catalog and gateway tests**

```python
def test_training_cost_requires_model_training_price(self) -> None:
    with self.assertRaises(CatalogValidationError) as captured:
        self.catalog.estimate_training_cost("deepseek", "deepseek-v4-flash", training_tokens=12)
    self.assertEqual("pricing_unavailable", captured.exception.code)

def test_fine_tuning_rejects_a_model_without_declared_capability(self) -> None:
    with self.assertRaises(ProviderError) as captured:
        self.gateway.submit_fine_tuning(self.request)
    self.assertEqual("capability_not_supported", captured.exception.code)
```

- [x] **Step 2: Run the focused tests and verify the expected red state**

Run: `python -m unittest tests.unit.test_provider_catalog tests.unit.test_provider_gateway -v`

Expected: failures because no training cost method or gateway method exists.

- [x] **Step 3: Add transport-neutral contracts and capability checks**

```python
@dataclass(frozen=True, slots=True)
class FineTuningRequest:
    provider_id: str
    model_id: str
    job_id: str
    dataset_path: Path
    dataset_sha256: str
    sample_count: int

@dataclass(frozen=True, slots=True)
class FineTuningSubmission:
    provider_job_id: str

@dataclass(frozen=True, slots=True)
class FineTuningStatus:
    state: str
    progress_percent: int | None = None
    artifact_id: str | None = None
    evaluation: Mapping[str, Any] | None = None
    retryable: bool = False

class FineTuningProviderAdapter(Protocol):
    def supports_fine_tuning(self, model_id: str) -> bool: ...
    def submit_fine_tuning(self, request: FineTuningRequest) -> FineTuningSubmission: ...
    def recover_fine_tuning_submission(self, client_job_id: str) -> FineTuningSubmission | None: ...
    def get_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus: ...
    def cancel_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus: ...
```

Extend `ModelPricing` with `training_price_per_million_tokens`, add a `TrainingCostEstimate` response, and add `ProviderCatalog.estimate_training_cost`. The gateway must require both provider and model `fine_tuning` capabilities before an adapter call, translate `AdapterError`, strictly require boolean capability responses, and require `FineTuningProviderAdapter` rather than assuming every chat adapter can train. A training adapter must reconcile by the client-generated job ID before it can receive a dataset, so an accepted remote submission cannot become locally untraceable after a write failure.

- [x] **Step 4: Implement a deterministic test-only fine-tuning adapter**

```python
def submit_fine_tuning(self, request: FineTuningRequest) -> FineTuningSubmission:
    self.submissions.append(request)
    return FineTuningSubmission(provider_job_id=f"test-ft-{request.job_id}")

def get_fine_tuning_job(self, provider_job_id: str) -> FineTuningStatus:
    return FineTuningStatus(
        state="completed",
        progress_percent=100,
        artifact_id=f"artifact-{provider_job_id}",
        evaluation={"status": "verified"},
    )
```

The deterministic provider is assembled only when `ServerConfig.mode == "test"`; production and development cannot use it.

- [x] **Step 5: Run focused tests and retain the invariant**

Run: `python -m unittest tests.unit.test_provider_catalog tests.unit.test_provider_gateway -v`

Expected: capability, price, adapter-unavailable, and test-only successful contract tests pass.

### Task 2: Stream Accepted Persona Records Into A Temporary Dataset

**Files:**
- Modify: `src/services/upload_service.py`
- Create: `src/services/training_dataset.py`
- Create: `tests/unit/test_training_dataset.py`
- Modify: `tests/unit/test_upload_service.py`

- [x] **Step 1: Write failing target-role and cleanup tests**

```python
def test_builds_only_accepted_persona_messages(self) -> None:
    dataset = self.builder.build(self.owner_id, self.persona_id, self.import_id)
    lines = dataset.path.read_text(encoding="utf-8").splitlines()
    self.assertEqual(2, dataset.sample_count)
    self.assertNotIn("user message", "\n".join(lines))
    self.assertNotIn("needs review", "\n".join(lines))

def test_rejects_a_truncated_or_empty_target_dataset(self) -> None:
    with self.assertRaises(TrainingDatasetError) as captured:
        self.builder.build(self.owner_id, self.persona_id, self.import_id)
    self.assertEqual("training_samples_insufficient", captured.exception.code)
```

- [x] **Step 2: Run the focused tests and verify the expected red state**

Run: `python -m unittest tests.unit.test_training_dataset tests.unit.test_upload_service -v`

Expected: import failure because the dataset builder and safe record iterator do not exist.

- [x] **Step 3: Add safe corrected-record streaming to `UploadService`**

```python
def iter_training_records(self, owner_id: str, import_id: str) -> Iterator[dict[str, str]]:
    """Yield corrected records while holding only the target import's payload lease."""
    # Snapshot immutable manifest metadata under self._lock.
    # Materialize one manifest file at a time under storage/training-source.
    # Select and validate its parser, then enumerate parser.stream_records().
    # Assign the same stable record ID as preview, apply encrypted corrections,
    # and yield role/review/content fields without retaining prior records.
```

Use the media-inspection per-import payload lease pattern, not `iter_payload`, so a large training read does not hold the service-wide lock. Check temporary free space before materializing each source file, map write failures to `training_dataset_storage_unavailable`, and delete each plaintext source in `finally`.

- [x] **Step 4: Add JSONL dataset construction with digest and deletion**

```python
for record in uploads.iter_training_records(owner_id, import_id):
    if record["sender_role"] != "persona" or record["review_state"] != "accepted":
        continue
    line = json.dumps({"messages": [{"role": "assistant", "content": record["content"]}]}, ensure_ascii=False)
    output.write(line + "\n")
    digest.update((line + "\n").encode("utf-8"))
```

Require at least two samples, reject missing participant mappings or no accepted persona coverage, cap each record and total JSONL byte count, and expose only `path`, `sha256`, `sample_count`, plus a rolling source-record count and digest to the service. The digest replaces an unbounded in-memory list of source IDs while retaining auditable dataset integrity. `TrainingDataset.cleanup()` must remove temporary JSONL even if provider submission raises.

- [x] **Step 5: Run focused tests and verify bounded, target-only behavior**

Run: `python -m unittest tests.unit.test_training_dataset tests.unit.test_upload_service -v`

Expected: accepted persona records are included, all other roles/states are excluded, temporary plaintext is removed, and another import upload proceeds while a dataset is built.

### Task 3: Persist And Validate Fine-Tuning Job Lifecycle

**Files:**
- Create: `src/domain/training_jobs.py`
- Modify: `src/services/database.py`
- Create: `src/services/training_repository.py`
- Create: `src/services/training_service.py`
- Create: `tests/unit/test_training_jobs.py`
- Create: `tests/unit/test_training_repository.py`
- Create: `tests/unit/test_training_service.py`

- [x] **Step 1: Write failing lifecycle and encryption tests**

```python
def test_completed_job_requires_verified_artifact_and_evaluation(self) -> None:
    with self.assertRaises(TrainingJobValidationError) as captured:
        self.running.complete(artifact_id="", evaluation={})
    self.assertEqual("training_result_unverified", captured.exception.code)

def test_repository_encrypts_dataset_metadata(self) -> None:
    self.repository.save(self.owner_id, self.job)
    database_bytes = self.database_path.read_bytes()
    self.assertNotIn(self.job.dataset_sha256.encode("ascii"), database_bytes)
```

- [x] **Step 2: Run the focused tests and verify the expected red state**

Run: `python -m unittest tests.unit.test_training_jobs tests.unit.test_training_repository tests.unit.test_training_service -v`

Expected: import failures because no job model, migration, repository, or service exists.

- [x] **Step 3: Define encrypted job state and migration 6**

```python
Migration(
    version=6,
    name="training_job_repository",
    statements=(
        """
        CREATE TABLE training_jobs (
            id TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL REFERENCES local_users(id) ON DELETE CASCADE,
            persona_id TEXT NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
            record_version INTEGER NOT NULL CHECK (record_version = 1),
            encrypted_payload BLOB NOT NULL CHECK (length(encrypted_payload) > 0)
        )
        """,
        "CREATE INDEX training_jobs_owner_persona_idx ON training_jobs(owner_id, persona_id)",
    ),
)
```

`TrainingJob` states are `pending`, `running`, `completed`, `failed`, and `cancelled`. The encrypted payload stores provider/model IDs, provider job ID, client submission intent, consent ID, import ID, dataset digest/count, timestamps, redacted provider failure code, a separate local plaintext-cleanup failure code, retryability, artifact ID, and evaluation. It never stores raw messages, plaintext dataset paths, credentials, or provider response bodies.

- [x] **Step 4: Implement `FineTuningService` preflight and lifecycle**

```python
authorization = self.consent_gate.authorize(
    owner_id, consent_id,
    persona_id=persona_id,
    import_id=import_id,
    provider_id=provider_id,
    model_id=model_id,
)
dataset = self.datasets.build(owner_id, persona_id, import_id)
try:
    estimate = self.catalog.estimate_training_cost(provider_id, model_id, training_tokens=dataset.estimated_tokens)
    self._require_consent_cost(authorization, estimate.estimated_cost)
    job = self.repository.save(owner_id, TrainingJob.pending(...))
    job = self.repository.save(owner_id, job.start_provider_submission(...))
    submission = self.gateway.submit_fine_tuning(job.to_provider_request(dataset))
    job = self.repository.save(owner_id, job.bind_provider_submission(submission.provider_job_id, ...))
    job = self.repository.save(owner_id, job.mark_running(submission.provider_job_id, ...))
finally:
    dataset.cleanup()
```

When submission or status polling fails, persist a redacted `failed` state only after a job exists. If the provider may have accepted a submission but its opaque ID cannot be saved, the persistent intent is reconciled by local job ID before cancellation or polling may close it. On polling, transition to `completed` only when artifact and evaluation are both present; otherwise transition to `failed` with `training_result_unverified`. A plaintext cleanup failure remains a separate encrypted local failure code and cannot overwrite a real provider completion, failure, or cancellation. Cancellation invokes the provider before recording `cancelled` for a running job. `delete_for_persona` cancels controlled jobs best-effort, deletes encrypted records, and returns an external-provider cleanup limitation count rather than claiming third-party deletion.

- [x] **Step 5: Run focused tests and verify state transitions**

Run: `python -m unittest tests.unit.test_training_jobs tests.unit.test_training_repository tests.unit.test_training_service -v`

Expected: owner isolation, encryption, scope mismatch, insufficient samples, unsupported capability, unconfigured adapter, validated completion, cancellation, and persona cleanup all pass.

### Task 4: Wire Versioned HTTP APIs And Lifecycle Ownership

**Files:**
- Modify: `src/server/application.py`
- Modify: `src/server/http.py`
- Modify: `tests/integration/test_http_api.py`
- Modify: `tests/integration/test_privacy_policy_contract.py`

- [x] **Step 1: Write failing HTTP contract tests**

```python
status, _, created = self.request(
    "POST", "/api/v1/training-jobs", authorized_headers,
    {"persona_id": persona_id, "import_id": import_id, "provider_id": "test", "model_id": "deterministic", "consent_id": consent_id},
)
self.assertEqual(202, status)
self.assertEqual("running", created["state"])

status, _, completed = self.request("GET", f"/api/v1/training-jobs/{created['id']}", authorized_headers)
self.assertEqual(200, status)
self.assertEqual("completed", completed["state"])
self.assertTrue(completed["artifact_id"])
self.assertEqual("verified", completed["evaluation"]["status"])
```

Also assert no provider submission on `capability_not_supported`, `provider_not_configured`, revoked/mismatched consent, cross-persona import, missing mapping, or insufficient accepted samples.

- [x] **Step 2: Run the integration tests and verify the expected red state**

Run: `python -m unittest tests.integration.test_http_api -v`

Expected: route-not-found failures for the new APIs.

- [x] **Step 3: Add application facade methods and exact routes**

```text
POST /api/v1/training-jobs/estimate
POST /api/v1/training-jobs
GET  /api/v1/training-jobs?persona_id=<id>
GET  /api/v1/training-jobs/<id>
POST /api/v1/training-jobs/<id>/cancel
```

All routes require owner authentication. Add training jobs to the same persona lifecycle lock as consent/import creation and deletion. Include redacted job metadata in `export_data`; update persona deletion response with `deleted_training_jobs` and `external_training_cleanup_limitations`.

- [x] **Step 4: Add stable HTTP error mappings**

```python
TRAINING_CONFLICT_CODES = {"consent_revoked", "consent_scope_mismatch", "training_job_closed", "training_job_conflict"}
TRAINING_UNPROCESSABLE_CODES = {
    "capability_not_supported", "training_samples_insufficient",
    "training_dataset_invalid", "training_result_unverified", "pricing_unavailable",
}
TRAINING_UNAVAILABLE_CODES = {"provider_not_configured", "provider_unavailable"}
```

Map unknown resources to `404`, malformed request data to `400`, preflight and optimistic-write conflicts to `409`, unsatisfied capability/dataset/price checks to `422`, unavailable provider to `503`, temporary storage exhaustion to `507`, and plaintext cleanup failure to `500`. Preserve the existing diagnostic ID behavior for unexpected exceptions.

- [x] **Step 5: Run HTTP and privacy contract tests**

Run: `python -m unittest tests.integration.test_http_api tests.integration.test_privacy_policy_contract -v`

Expected: authenticated lifecycle works only in test mode with the deterministic adapter; production-style missing capabilities remain explicit failures.

### Task 5: Document The Actual Boundary And Run Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/privacy_policy.md`
- Modify: `tests/integration/test_dependency_groups.py`
- Modify: `docs/superpowers/plans/2026-08-11-p2-07-capability-gated-fine-tuning.md`

- [x] **Step 1: Add documentation contract tests**

```python
for required in (
    "fine_tuning", "persona_text", "accepted", "provider_transfer",
    "capability_not_supported", "artifact", "evaluation",
):
    self.assertIn(required, policy_text)
```

- [x] **Step 2: Run the documentation tests and verify the expected red state**

Run: `python -m unittest tests.integration.test_dependency_groups tests.integration.test_privacy_policy_contract -v`

Expected: policy/README assertions fail until the new boundary is disclosed.

- [x] **Step 3: Document scope without overstating provider support**

State that the server sends only accepted persona-authored text after a dedicated provider/model/import consent matches. Explain that the service stores encrypted job metadata, removes temporary plaintext datasets, and does not claim a model can fine tune until both catalog capability and an installed adapter are present. Do not list any current hosted provider as supported unless its concrete adapter is implemented and tested in this repository.

- [x] **Step 4: Update the plan checkboxes and run full verification**

Run:

```powershell
python -m compileall -q src tests
git diff --check
npm test
codegraph sync
codegraph status
```

Expected: focused and full tests pass, compile/diff checks exit 0, and CodeGraph reports the P2-07 worktree index is up to date.

- [x] **Step 5: Request independent review and prepare the feature branch for user acceptance**

Do not merge or push. Record the commit only after the full checks and independent review report no unresolved critical or important finding. Present the concrete API behavior, test counts, and any truthful remaining provider limitation for user acceptance.
