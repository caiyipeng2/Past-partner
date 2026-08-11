# Media Pipeline Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an authenticated owner inspect completed image, audio, and video imports locally, returning verified format metadata without sending raw media to a provider.

**Architecture:** Add a small optional-dependency media inspector that uses Pillow for images and a fail-closed `ffprobe` invocation for audio/video. `UploadService` will decrypt one manifest file boundary at a time into a server-owned temporary file, inspect it, and always remove that file before returning. A versioned GET endpoint exposes only structured metadata; provider consent remains reserved for later external-model transmission.

**Tech Stack:** Python standard library, Pillow optional parser dependency, local `ffprobe`, existing encrypted `UploadService`, standard-library HTTP server, `unittest`.

---

## File Structure

- Create: `src/preprocessing/media_inspector.py` - local metadata inspection and stable, fail-closed error contract.
- Modify: `src/services/upload_service.py` - materialize one decrypted manifest file at a time, call the inspector, and remove the temporary file in `finally`.
- Modify: `src/server/application.py` - expose the upload service inspection operation to authenticated request handlers.
- Modify: `src/server/http.py` - add the `GET /api/v1/imports/{import_id}/media-inspection` route and status mapping.
- Modify: `requirements-parsers.txt` - declare Pillow in the existing optional parser profile.
- Modify: `tests/integration/test_dependency_groups.py` - preserve the exact parser-profile contract.
- Create: `tests/unit/test_media_inspector.py` - unit tests for images, audio/video metadata normalization, type mismatch, and unavailable processors.
- Modify: `tests/integration/test_http_api.py` - API-level completed-upload inspection test plus owner-scoped error assertions.
- Modify: `README.md` and `docs/privacy_policy.md` - state the local-only behavior, optional dependencies, and no-provider-transfer boundary.

### Task 1: Lock the inspection contract with failing tests

**Files:**
- Create: `tests/unit/test_media_inspector.py`
- Modify: `tests/integration/test_dependency_groups.py`
- Modify: `tests/integration/test_http_api.py`

- [ ] **Step 1: Write the failing unit tests for verified media metadata**

```python
def test_inspects_a_png_from_its_bytes_not_its_name(self) -> None:
    result = MediaInspector().inspect(image_path, "image/png")
    self.assertEqual("image", result["kind"])
    self.assertEqual("image/png", result["detected_media_type"])
    self.assertEqual({"width": 2, "height": 3}, result["dimensions"])

def test_rejects_declared_audio_when_probe_reports_a_video_stream(self) -> None:
    inspector = MediaInspector(av_probe=lambda _: VIDEO_PROBE)
    with self.assertRaisesRegex(MediaInspectionError, "media_type_mismatch"):
        inspector.inspect(video_path, "audio/ogg")
```

- [ ] **Step 2: Run the new unit test module and confirm RED**

Run: `python -m unittest tests.unit.test_media_inspector -v`

Expected: import failure because `src.preprocessing.media_inspector` does not exist yet.

- [ ] **Step 3: Add the failing HTTP contract and dependency-profile assertion**

```python
status, _, inspection = self.request(
    "GET", f"/api/v1/imports/{job['id']}/media-inspection"
)
self.assertEqual(200, status)
self.assertEqual("local_metadata", inspection["processing_mode"])
self.assertFalse(inspection["provider_transfer"])
self.assertEqual("image", inspection["files"][0]["kind"])
self.assertFalse(list((self.data_root / "media-inspection").glob("*")))

self.assertIn("Pillow>=10.0.0", self._entries("requirements-parsers.txt"))
```

- [ ] **Step 4: Run the focused test modules and confirm the intended failures**

Run: `python -m unittest tests.unit.test_media_inspector tests.integration.test_http_api tests.integration.test_dependency_groups -v`

Expected: the new endpoint and inspector tests fail because the API and production class are absent; the dependency test fails because Pillow is not yet declared.

### Task 2: Implement the local media inspector

**Files:**
- Create: `src/preprocessing/media_inspector.py`
- Modify: `requirements-parsers.txt`

- [ ] **Step 1: Implement a narrow, fail-closed inspection interface**

```python
class MediaInspectionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MediaInspector:
    def __init__(self, av_probe: Callable[[Path], Mapping[str, Any]] | None = None) -> None:
        self._av_probe = av_probe or _ffprobe

    def inspect(self, source: Path, declared_media_type: str) -> dict[str, Any]:
        category = _declared_category(declared_media_type)
        if category == "image":
            return self._inspect_image(source, declared_media_type)
        if category in {"audio", "video"}:
            return self._inspect_av(source, declared_media_type, category)
        raise MediaInspectionError("unsupported_media_type", "media type is not supported for inspection")
```

`_inspect_image` must obtain the detected format and dimensions from Pillow rather than the filename, reject unavailable Pillow as `media_processor_unavailable`, and reject a declared MIME mismatch as `media_type_mismatch`. `_ffprobe` must invoke a local executable with `shell=False`, a finite timeout, a fixed field list, and JSON output; missing or failed processors must not be treated as successful analysis.

- [ ] **Step 2: Declare the optional image parser dependency**

```text
# Local image metadata inspection; loaded lazily so core-only installations still start.
Pillow>=10.0.0
```

Place the entry after `pypdf>=5.0.0` in `requirements-parsers.txt` and update the exact dependency-group expectation.

- [ ] **Step 3: Run the inspector and dependency tests to confirm GREEN**

Run: `python -m unittest tests.unit.test_media_inspector tests.integration.test_dependency_groups -v`

Expected: all tests pass; audio/video tests use an injected probe result and do not require a machine-global ffprobe executable.

### Task 3: Connect encrypted imports to the authenticated API

**Files:**
- Modify: `src/services/upload_service.py`
- Modify: `src/server/application.py`
- Modify: `src/server/http.py`
- Modify: `tests/integration/test_http_api.py`

- [ ] **Step 1: Add a failing API test for a completed PNG import**

```python
status, _, unavailable = self.request(
    "GET", f"/api/v1/imports/{unfinished_job['id']}/media-inspection"
)
self.assertEqual(409, status)
self.assertEqual("media_inspection_unavailable", unavailable["error"]["code"])
```

- [ ] **Step 2: Add `UploadService.inspect_media` with one-file-at-a-time materialization**

```python
def inspect_media(self, owner_id: str, import_id: str) -> dict[str, Any]:
    job = self.imports.get(owner_id, import_id)
    if job.state is not ImportState.UPLOADED or not self.payload_path(import_id).is_file():
        raise UploadError("media_inspection_unavailable", "inspection requires a completed uploaded import")
    # Split decrypted bytes strictly on manifest boundaries; no client path is reused.
    # Each server-owned temporary file is deleted in finally, even when inspection fails.
```

The response must include `import_id`, `state`, `processing_mode: "local_metadata"`, `provider_transfer: false`, and one metadata object per manifest file. It must reject extra or missing decrypted bytes as `payload_corrupt`, preserve owner scoping through the existing repository lookup, and never serialize raw bytes or a local path.

- [ ] **Step 3: Route the operation and map stable failures**

```python
_MEDIA_INSPECTION_PATH = re.compile(
    r"^/api/v1/imports/([A-Za-z0-9._-]+)/media-inspection$"
)

elif match := _MEDIA_INSPECTION_PATH.fullmatch(path):
    self._json(
        HTTPStatus.OK,
        self.server.application.inspect_import_media(self.owner_id, match.group(1)),
    )
```

Map `media_inspection_unavailable` to 409, `unsupported_media_type` to 415, `media_type_mismatch` and `media_metadata_invalid` to 422, and `media_processor_unavailable` to 503. Keep the existing `/preview` behavior unchanged.

- [ ] **Step 4: Run focused API tests to confirm GREEN**

Run: `python -m unittest tests.integration.test_http_api tests.unit.test_media_inspector -v`

Expected: completed image imports return local metadata; unfinished imports and unsupported inputs return the specified stable HTTP errors; the server-owned `media-inspection` directory is empty after each request.

### Task 4: Document, verify, and run the real-media acceptance path

**Files:**
- Modify: `README.md`
- Modify: `docs/privacy_policy.md`
- Modify: `docs/superpowers/plans/2026-08-11-media-pipeline-validation.md`

- [ ] **Step 1: Document scope without overstating model capability**

Add the parser-profile install command and explain that this endpoint performs local format metadata inspection only. State that it does not perform OCR, ASR, semantic video understanding, or provider transfer; those later actions must use the P2-06 consent gate and a provider-specific transport.

- [ ] **Step 2: Update privacy disclosure**

Document that a completed import is briefly decrypted into a server-owned temporary file for inspection and deleted before the response; only metadata is returned. Do not claim automatic deletion of source imports or external provider processing.

- [ ] **Step 3: Verify changed behavior and repository quality**

Run:

```text
python -m unittest tests.unit.test_media_inspector tests.integration.test_http_api tests.integration.test_dependency_groups -v
npm test
python -m compileall -q src tests
git diff --check
codegraph sync
codegraph status
```

Expected: all focused and full tests pass, compilation and diff checks exit 0, and CodeGraph reports the worktree index is up to date.

- [ ] **Step 4: Re-run a public-media acceptance check**

Use the already-downloaded temporary JPEG, Ogg, and WebM samples against a local test server. Confirm JPEG, Vorbis audio, and VP9/Opus video metadata are returned by `GET /api/v1/imports/{import_id}/media-inspection`; confirm the result reports `provider_transfer: false` for all three. Do not send any sample to a provider.
