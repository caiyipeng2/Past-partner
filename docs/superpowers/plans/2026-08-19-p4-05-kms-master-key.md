# P4-05 KMS-Backed Master Key Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit KMS-backed master-key source that wraps a random 256-bit data key with an AWS KMS-compatible backend, persists only the encrypted key blob, and fails closed on every unavailable or malformed path.

**Architecture:** Keep `MasterKeyProvider` as the encryption boundary. `KmsMasterKeyProvider` owns an atomic encrypted-key file and receives an injected `KmsBackend`; `AwsKmsBackend` is a lazy boto3 adapter so core installs remain usable without cloud SDKs. `ServerConfig` selects `auto`, `environment`, `dpapi`, or `kms`; explicit KMS configuration is validated before `Application.from_config` constructs sensitive repositories. Existing environment-key and Windows DPAPI behavior remains unchanged.

**Tech Stack:** Python 3.11+, standard-library file/locking primitives, optional boto3 from `requirements-storage.txt`, existing `MasterKeyProvider`, unittest, CodeGraph.

---

### Task 1: Define KMS provider and configuration contracts with failing tests

**Files:**
- Modify: `tests/unit/test_master_key_provider.py`
- Modify: `tests/unit/test_server_config.py`
- Modify: `tests/unit/test_application_wiring.py`

- [x] **Step 1: Write failing KMS provider tests**

Add a fake backend with `encrypt(key_id, plaintext)` and `decrypt(key_id, ciphertext)` methods. Test that `KmsMasterKeyProvider` provisions exactly 32 random bytes, writes only encrypted bytes, reloads through `decrypt`, rejects a missing ciphertext when auto-provision is disabled, rejects wrong decrypted length, maps backend exceptions without echoing key IDs or provider messages, and concurrent provisioning loads the winning file.

- [x] **Step 2: Write failing configuration and factory tests**

Add tests for `PAST_PARTNER_MASTER_KEY_SOURCE=kms`, required `PAST_PARTNER_MASTER_KEY_KMS_KEY_ID`, default ciphertext path under `<data-dir>/secrets/master-key.kms`, production rejection of non-HTTPS KMS endpoints, explicit-source precedence, invalid source rejection, and `Application.from_config` forwarding the typed KMS settings. Existing environment and DPAPI tests must remain unchanged.

- [x] **Step 3: Run focused tests to verify RED**

Run:

```powershell
python -m unittest tests.unit.test_master_key_provider tests.unit.test_server_config tests.unit.test_application_wiring -v
```

Expected: the new KMS tests fail because the provider, configuration fields, and factory arguments do not yet exist; the existing tests remain green.

### Task 2: Implement the KMS provider and lazy AWS-compatible backend

**Files:**
- Modify: `src/services/master_key.py`
- Modify: `tests/unit/test_master_key_provider.py`

- [x] **Step 1: Add the minimal provider contract**

Define `KmsBackend`, `AwsKmsBackend`, and `KmsMasterKeyProvider`. `AwsKmsBackend` must import boto3 only when instantiated, call `kms.encrypt(KeyId=..., Plaintext=...)` and `kms.decrypt(KeyId=..., CiphertextBlob=...)`, and convert import/client/remote failures into existing redacted `MasterKeyConfigurationError` or `MasterKeyUnavailableError` messages without including provider text, key IDs, ciphertext, or plaintext.

- [x] **Step 2: Implement atomic encrypted-key persistence**

Use the existing DPAPI pattern: load an existing regular file with a bounded size; decrypt and validate exactly 32 bytes; when `auto_provision=True`, generate 32 random bytes, encrypt them, write and flush a temporary file, atomically link it into place, and on `FileExistsError` discard the candidate and load the winner. Never write plaintext key bytes to disk or logs.

- [x] **Step 3: Implement source selection in `build_master_key_provider`**

Add explicit `master_key_source` and KMS keyword arguments. In `auto`, preserve current environment-first then Windows-development-DPAPI behavior and select KMS only when a KMS key ID is configured. In explicit `kms`, require a key ID and use the default `<data-dir>/secrets/master-key.kms` path when none is supplied. Missing optional boto3 or missing KMS configuration must fail only when the KMS provider is selected, never silently fall back to DPAPI or environment.

- [x] **Step 4: Run focused provider tests to verify GREEN**

Run:

```powershell
python -m unittest tests.unit.test_master_key_provider -v
```

Expected: all existing and new provider tests pass without network access or installed boto3.

### Task 3: Wire validated configuration, application startup, and documentation

**Files:**
- Modify: `src/server/config.py`
- Modify: `src/server/application.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/privacy_policy.md`
- Modify: `tests/unit/test_server_config.py`
- Modify: `tests/unit/test_application_wiring.py`
- Create: `tests/integration/test_kms_master_key.py`

- [x] **Step 1: Add typed KMS settings and environment parsing**

Add `master_key_source`, `master_key_kms_key_id`, `master_key_kms_ciphertext_file`, `master_key_kms_region`, `master_key_kms_endpoint`, and `master_key_kms_auto_provision` to `ServerConfig`. Parse `PAST_PARTNER_MASTER_KEY_SOURCE`, `PAST_PARTNER_MASTER_KEY_KMS_KEY_ID`, `PAST_PARTNER_MASTER_KEY_KMS_CIPHERTEXT_FILE`, `PAST_PARTNER_MASTER_KEY_KMS_REGION`, `PAST_PARTNER_MASTER_KEY_KMS_ENDPOINT`, and `PAST_PARTNER_MASTER_KEY_KMS_AUTO_PROVISION`. Validate source values, key ID/control characters, endpoint URL credentials/query/fragment, and require HTTPS outside development/test loopback.

- [x] **Step 2: Pass validated settings into application startup**

Update `Application.from_config` to pass the typed KMS arguments to `build_master_key_provider`. Keep the existing environment and DPAPI construction paths behaviorally identical.

- [x] **Step 3: Add opt-in integration coverage and documentation**

Create an integration test that skips unless `PAST_PARTNER_KMS_TEST_ENABLED=1` and explicit disposable KMS settings are present; it must inject a fake backend rather than contact a real account by default. Document KMS source selection, encrypted-file lifecycle, optional boto3 installation, HTTPS requirement, no plaintext backup, and fail-closed behavior. Do not expose key IDs, endpoints, ciphertext paths, or credentials in API responses or logs.

- [x] **Step 4: Run configuration, wiring, and opt-in tests**

Run:

```powershell
python -m unittest tests.unit.test_master_key_provider tests.unit.test_server_config tests.unit.test_application_wiring tests.integration.test_kms_master_key -v
```

Expected: all tests pass and the opt-in integration test skips only when explicit test configuration is absent.

### Task 4: Repository-wide verification and acceptance handoff

**Files:**
- No source changes expected unless a test reveals a genuine P4-05 regression.

- [x] **Step 1: Run focused, full, Web, syntax, and CodeGraph checks**

Run:

```powershell
python -m unittest tests.unit.test_master_key_provider tests.unit.test_server_config tests.unit.test_application_wiring tests.integration.test_kms_master_key -q
python -m unittest discover -s tests -p "test*.py" -q
node --test tests/web_workspace_test.mjs
python -m py_compile src/services/master_key.py src/server/config.py src/server/application.py
git diff --check
codegraph sync
```

Use a no-8.3-alias temporary directory for Windows full-suite runs if required. Report any environment-only failures separately and do not attribute them to P4-05 without reproduction.

- [x] **Step 2: Inspect the security boundary**

Run:

```powershell
rg -n "KmsMasterKeyProvider|AwsKmsBackend|build_master_key_provider|MASTER_KEY_KMS|master-key\.kms" src tests README.md .env.example docs
```

Confirm that plaintext master-key bytes never enter a file path, log message, HTTP response, or client configuration, and that explicit KMS selection cannot silently fall back to another provider.

- [ ] **Step 3: Commit and prepare user acceptance without merging**

Commit the implementation on `codex/p4-05-kms-master-key`, report the commit list and test evidence, and wait for explicit acceptance. Only after acceptance may the branch be fast-forwarded into `main`, re-tested, pushed to `origin/main`, and cleaned up.
