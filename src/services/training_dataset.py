"""Bounded-memory construction of temporary, target-role-only fine-tuning JSONL."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Iterator, Mapping
from uuid import uuid4

from src.services.import_repository import ImportRepositoryError
from src.services.import_service import ImportNotFoundError
from src.services.plaintext_lease import PlaintextLeaseRegistry
from src.services.storage import StorageLayout
from src.services.upload_service import (
    DEFAULT_TRAINING_RECORD_BYTES,
    UploadError,
    UploadService,
)


DEFAULT_MIN_TRAINING_SAMPLES = 2
DEFAULT_MAX_RECORD_BYTES = 64 * 1024
DEFAULT_MAX_DATASET_BYTES = 3 * 1024**3
_STORAGE_CHECK_BYTES = 64 * 1024


class TrainingDatasetError(ValueError):
    """A stable failure raised before a provider receives any training content."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TrainingDataset:
    """A plaintext dataset lease that the caller must remove after provider handoff."""

    path: Path
    sha256: str
    sample_count: int
    estimated_tokens: int
    source_record_count: int
    source_record_digest: str

    def cleanup(self) -> None:
        try:
            PlaintextLeaseRegistry.delete_and_release(self.path)
        except OSError as exc:
            raise TrainingDatasetError(
                "training_dataset_cleanup_failed",
                "temporary training dataset could not be removed",
            ) from exc


class TrainingDatasetBuilder:
    """Build a JSONL file from one approved persona import without retaining records."""

    def __init__(
        self,
        storage: StorageLayout,
        uploads: UploadService,
        *,
        min_samples: int = DEFAULT_MIN_TRAINING_SAMPLES,
        max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
        max_dataset_bytes: int = DEFAULT_MAX_DATASET_BYTES,
    ) -> None:
        self.storage = storage
        self.uploads = uploads
        self.min_samples = _positive_limit(min_samples, "min_samples")
        self.max_record_bytes = _positive_limit(max_record_bytes, "max_record_bytes")
        if self.max_record_bytes > DEFAULT_TRAINING_RECORD_BYTES:
            raise ValueError(
                f"max_record_bytes cannot exceed {DEFAULT_TRAINING_RECORD_BYTES} for safe streaming"
            )
        self.max_dataset_bytes = _positive_limit(max_dataset_bytes, "max_dataset_bytes")
        self._stale_cleanup_failures = self._cleanup_stale_datasets()

    def build(self, owner_id: str, persona_id: str, import_id: str) -> TrainingDataset:
        """Create a temporary JSONL lease after local ownership checks.

        The provider contract gets only this controlled path plus its digest. Raw
        import content is never returned here and both source materializations and
        partially written datasets are removed on every failure path.
        """

        self._require_stale_cleanup()

        try:
            job = self.uploads.imports.get(owner_id, import_id)
        except ImportNotFoundError as exc:
            raise TrainingDatasetError(
                "training_import_not_found",
                "training import does not exist for this owner",
            ) from exc
        except (ImportRepositoryError, ValueError) as exc:
            # Import repository failures can include encrypted metadata corruption
            # or invalid owner values. Neither implementation detail belongs in a
            # training API response before a provider receives any content.
            raise TrainingDatasetError(
                "training_import_unavailable",
                "training import metadata is unavailable",
            ) from exc
        if job.persona_id != persona_id:
            raise TrainingDatasetError(
                "training_import_persona_mismatch",
                "training import does not belong to the requested persona",
            )

        destination = self.storage.object_path("training-datasets", uuid4().hex, ".jsonl")
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        records = self.uploads.iter_training_records(
            owner_id,
            import_id,
            max_record_bytes=self.max_record_bytes,
        )
        # Register before any file exists so another local builder's startup cleanup
        # cannot mistake a concurrently-created temporary file for a crash leftover.
        PlaintextLeaseRegistry.reserve(temporary)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            source_digest = hashlib.sha256()
            sample_count = 0
            estimated_tokens = 0
            dataset_bytes = 0
            bytes_since_storage_check = 0

            self._require_storage(destination, 1)
            with temporary.open("x", encoding="utf-8", newline="\n") as output:
                for record in records:
                    if (
                        record.get("sender_role") != "persona"
                        or record.get("review_state") != "accepted"
                    ):
                        continue
                    record_id = record.get("record_id")
                    content = record.get("content")
                    if not isinstance(record_id, str) or not isinstance(content, str):
                        raise TrainingDatasetError(
                            "training_dataset_invalid",
                            "accepted training record has invalid fields",
                        )
                    if not content.strip():
                        # Attachment-only exports can be approved for retention, but
                        # they are not persona-authored text examples for this contract.
                        continue
                    content_bytes = content.encode("utf-8")
                    if len(content_bytes) > self.max_record_bytes:
                        raise TrainingDatasetError(
                            "training_record_too_large",
                            "accepted training record exceeds the configured byte limit",
                        )
                    line = json.dumps(
                        {"messages": [{"role": "assistant", "content": content}]},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ) + "\n"
                    line_bytes = line.encode("utf-8")
                    if dataset_bytes + len(line_bytes) > self.max_dataset_bytes:
                        raise TrainingDatasetError(
                            "training_dataset_too_large",
                            "training dataset exceeds the configured byte limit",
                        )
                    bytes_since_storage_check += len(line_bytes)
                    if bytes_since_storage_check >= _STORAGE_CHECK_BYTES:
                        self._require_storage(destination, bytes_since_storage_check)
                        bytes_since_storage_check = 0
                    output.write(line)
                    digest.update(line_bytes)
                    source_digest.update(f"{record_id}\n".encode("ascii"))
                    sample_count += 1
                    dataset_bytes += len(line_bytes)
                    # This is deliberately a coarse estimate, not a fabricated model
                    # tokenizer result. Model-specific pricing and confirmation remain
                    # separate preflight checks in the training service.
                    estimated_tokens += max(1, (len(content_bytes) + 3) // 4)

                output.flush()
                os.fsync(output.fileno())

            if sample_count < self.min_samples:
                raise TrainingDatasetError(
                    "training_samples_insufficient",
                    "training requires enough accepted persona-authored text samples",
                )
            PlaintextLeaseRegistry.promote(temporary, destination)
            return TrainingDataset(
                path=destination,
                sha256=digest.hexdigest(),
                sample_count=sample_count,
                estimated_tokens=estimated_tokens,
                source_record_count=sample_count,
                source_record_digest=source_digest.hexdigest(),
            )
        except UploadError as exc:
            self._cleanup_failed_build(temporary, destination)
            raise TrainingDatasetError(exc.code, str(exc)) from exc
        except TrainingDatasetError:
            self._cleanup_failed_build(temporary, destination)
            raise
        except OSError as exc:
            self._cleanup_failed_build(temporary, destination)
            raise TrainingDatasetError(
                "training_dataset_storage_unavailable",
                "temporary training dataset storage is unavailable",
            ) from exc
        except Exception as exc:
            self._cleanup_failed_build(temporary, destination)
            raise TrainingDatasetError(
                "training_dataset_invalid",
                "training dataset could not be built safely",
            ) from exc
        finally:
            # Explicitly close the source iterator so a rejected record or output
            # write failure cannot leave a materialized plaintext import on Windows.
            try:
                records.close()
            except UploadError as exc:
                raise TrainingDatasetError(exc.code, str(exc)) from exc
            except Exception as exc:
                raise TrainingDatasetError(
                    "training_dataset_cleanup_failed",
                    "temporary training source could not be removed",
                ) from exc

    def _require_storage(self, destination: Path, required_bytes: int) -> None:
        try:
            if shutil.disk_usage(destination.parent).free < required_bytes:
                raise TrainingDatasetError(
                    "training_dataset_storage_unavailable",
                    "temporary training dataset storage is insufficient",
                )
        except OSError as exc:
            raise TrainingDatasetError(
                "training_dataset_storage_unavailable",
                "temporary training dataset storage is unavailable",
            ) from exc

    @staticmethod
    def _cleanup_failed_build(temporary: Path, destination: Path) -> None:
        cleanup_error: OSError | None = None
        for candidate in (temporary, destination):
            try:
                PlaintextLeaseRegistry.delete_and_release(candidate)
            except OSError as exc:
                # Try both paths: a failed temporary deletion must not prevent a
                # best-effort removal of a partially promoted destination file.
                cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise TrainingDatasetError(
                "training_dataset_cleanup_failed",
                "temporary training dataset could not be removed",
            ) from cleanup_error

    def _require_stale_cleanup(self) -> None:
        if not self._stale_cleanup_failures:
            return
        self._stale_cleanup_failures = self._cleanup_stale_datasets()
        if self._stale_cleanup_failures:
            raise TrainingDatasetError(
                "training_dataset_cleanup_failed",
                "stale plaintext training data could not be removed",
            )

    def _cleanup_stale_datasets(self) -> tuple[Path, ...]:
        directory = self.storage.object_path("training-datasets", "sentinel").parent
        if not directory.is_dir():
            return ()
        failures: list[Path] = []
        for candidate in (*directory.glob("*.jsonl"), *directory.glob(".*.tmp")):
            try:
                PlaintextLeaseRegistry.delete_if_stale(candidate)
            except OSError:
                # The caller retries before a provider handoff. This retains the
                # failure as an explicit privacy condition instead of silently
                # continuing while a known plaintext file remains on disk.
                failures.append(candidate)
        return tuple(failures)


def _positive_limit(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return value
