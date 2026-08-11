"""P2-07 dataset construction must not leak unreviewed or non-persona text."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.import_repository import ImportRepository, ImportRepositoryError
from src.services.import_service import ImportService
from src.services.local_auth import LocalAuthService
from src.services.master_key import (
    EnvironmentMasterKeyProvider,
    MASTER_KEY_BYTES,
    MASTER_KEY_ENV_VAR,
)
from src.services.persona_repository import PersonaRepository
from src.services.persona_service import PersonaService
from src.services.storage import StorageLayout
from src.services.training_dataset import TrainingDatasetBuilder, TrainingDatasetError
from src.services.upload_service import UploadService
from src.domain.messages import NormalizedMessage


class TrainingDatasetBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"t" * MASTER_KEY_BYTES).decode("ascii")
        encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        personas = PersonaService(PersonaRepository(self.layout.database_path(), encryption))
        auth = LocalAuthService(self.layout.database_path(), encryption, mode="test")
        self.owner_id = auth.owner_id
        self.persona = personas.create(self.owner_id, "小雨", "friend")
        imports = ImportService(ImportRepository(self.layout.database_path(), encryption), personas)
        self.uploads = UploadService(self.layout, imports, encryption, read_block_bytes=8)
        self.datasets = TrainingDatasetBuilder(self.layout, self.uploads)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @staticmethod
    def _digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    def _completed_import(self, messages: list[dict[str, str]]) -> str:
        payload = b"".join(
            json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
            for message in messages
        )
        job = self.uploads.imports.create(
            self.owner_id,
            self.persona.id,
            "chat.jsonl",
            len(payload),
            "application/x-ndjson",
        )
        self.uploads.put_chunk(
            self.owner_id,
            job.id,
            0,
            len(payload),
            self._digest(payload),
            io.BytesIO(payload),
        )
        self.uploads.complete(self.owner_id, job.id, self._digest(payload))
        return job.id

    def _accept_records(self, import_id: str, accepted_indexes: set[int]) -> list[dict[str, object]]:
        preview = self.uploads.preview(self.owner_id, import_id, max_records=100)
        corrections = [
            {
                "record_id": record["record_id"],
                "review_state": "accepted",
                "fields": {},
            }
            for index, record in enumerate(preview["records"])
            if index in accepted_indexes
        ]
        self.uploads.save_corrections(self.owner_id, import_id, corrections)
        return preview["records"]

    def test_builds_only_accepted_persona_messages_and_cleans_plaintext(self) -> None:
        import_id = self._completed_import(
            [
                {"sender": "persona", "message": "第一条人物消息", "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "user", "message": "用户消息不能进入训练", "timestamp": "2026-08-11T10:01:00+08:00"},
                {"sender": "persona", "message": "未审核消息不能进入训练", "timestamp": "2026-08-11T10:02:00+08:00"},
                {"sender": "other", "message": "其他人消息不能进入训练", "timestamp": "2026-08-11T10:03:00+08:00"},
                {"sender": "persona", "message": "第二条人物消息", "timestamp": "2026-08-11T10:04:00+08:00"},
            ]
        )
        self.uploads.set_participant_mapping(
            self.owner_id,
            import_id,
            {"persona": "persona", "user": "user", "other": "other"},
        )
        records = self._accept_records(import_id, {0, 1, 3, 4})

        dataset = self.datasets.build(self.owner_id, self.persona.id, import_id)

        lines = dataset.path.read_text(encoding="utf-8").splitlines()
        serialized = "\n".join(lines)
        self.assertEqual(2, dataset.sample_count)
        self.assertEqual(self._digest(dataset.path.read_bytes()), dataset.sha256)
        expected_source_digest = hashlib.sha256()
        for record in (records[0], records[4]):
            expected_source_digest.update(f"{record['record_id']}\n".encode("ascii"))
        self.assertEqual(2, dataset.source_record_count)
        self.assertEqual(expected_source_digest.hexdigest(), dataset.source_record_digest)
        self.assertIn("第一条人物消息", serialized)
        self.assertIn("第二条人物消息", serialized)
        self.assertNotIn("用户消息不能进入训练", serialized)
        self.assertNotIn("未审核消息不能进入训练", serialized)
        self.assertNotIn("其他人消息不能进入训练", serialized)
        self.assertEqual(
            "assistant",
            json.loads(lines[0])["messages"][0]["role"],
        )
        self.assertFalse(list((self.root / "training-source").glob("*.bin")))

        dataset.cleanup()

        self.assertFalse(dataset.path.exists())

    def test_requires_a_participant_mapping_before_decrypting_training_samples(self) -> None:
        import_id = self._completed_import(
            [
                {"sender": "persona", "message": "第一条", "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "persona", "message": "第二条", "timestamp": "2026-08-11T10:01:00+08:00"},
            ]
        )

        with patch.object(
            self.uploads,
            "_iter_completed_payload_while_leased",
            side_effect=AssertionError("missing mapping must not decrypt the import payload"),
        ):
            with self.assertRaises(TrainingDatasetError) as captured:
                self.datasets.build(self.owner_id, self.persona.id, import_id)

        self.assertEqual("training_mapping_unavailable", captured.exception.code)
        self.assertFalse(list((self.root / "training-source").glob("*.bin")))
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))

    def test_maps_import_preflight_failures_to_a_stable_dataset_error(self) -> None:
        """Repository corruption and malformed owner data stay behind this boundary."""
        for failure in (
            ImportRepositoryError("import_decryption_failed", "encrypted import record is invalid"),
            ValueError("owner_id must be a non-empty string"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with patch.object(self.uploads.imports, "get", side_effect=failure):
                    with self.assertRaises(TrainingDatasetError) as captured:
                        self.datasets.build(self.owner_id, self.persona.id, "missing-import")

                self.assertEqual("training_import_unavailable", captured.exception.code)

    def test_requires_a_persona_mapping_before_decrypting_training_samples(self) -> None:
        """A non-empty map without a target role has no valid training subject."""
        import_id = self._completed_import(
            [
                {"sender": "source", "message": "第一条", "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "source", "message": "第二条", "timestamp": "2026-08-11T10:01:00+08:00"},
            ]
        )
        self.uploads.set_participant_mapping(self.owner_id, import_id, {"source": "user"})

        with patch.object(
            self.uploads,
            "_iter_completed_payload_while_leased",
            side_effect=AssertionError("a missing target mapping must not decrypt the import payload"),
        ):
            with self.assertRaises(TrainingDatasetError) as captured:
                self.datasets.build(self.owner_id, self.persona.id, import_id)

        self.assertEqual("training_persona_mapping_unavailable", captured.exception.code)
        self.assertFalse(list((self.root / "training-source").glob("*.bin")))
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))

    def test_sender_correction_cannot_promote_a_user_record_into_the_target_role(self) -> None:
        """Role selection follows the accepted participant map, not editable display data."""
        import_id = self._completed_import(
            [
                {"sender": "persona", "message": "人物样本一", "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "user", "message": "不得伪装进入训练", "timestamp": "2026-08-11T10:01:00+08:00"},
                {"sender": "persona", "message": "人物样本二", "timestamp": "2026-08-11T10:02:00+08:00"},
            ]
        )
        self.uploads.set_participant_mapping(
            self.owner_id,
            import_id,
            {"persona": "persona", "user": "user"},
        )
        preview = self.uploads.preview(self.owner_id, import_id, max_records=100)
        self.uploads.save_corrections(
            self.owner_id,
            import_id,
            [
                {"record_id": record["record_id"], "review_state": "accepted", "fields": {}}
                if index != 1
                else {
                    "record_id": record["record_id"],
                    "review_state": "accepted",
                    "fields": {"sender_id": "persona"},
                }
                for index, record in enumerate(preview["records"])
            ],
        )

        dataset = self.datasets.build(self.owner_id, self.persona.id, import_id)

        serialized = dataset.path.read_text(encoding="utf-8")
        self.assertEqual(2, dataset.sample_count)
        self.assertIn("人物样本一", serialized)
        self.assertIn("人物样本二", serialized)
        self.assertNotIn("不得伪装进入训练", serialized)
        dataset.cleanup()

    def test_rejects_an_accepted_record_over_the_configured_byte_limit(self) -> None:
        """A huge individual message cannot produce an oversized provider row."""
        import_id = self._completed_import(
            [
                {"sender": "persona", "message": "x" * 32, "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "persona", "message": "第二条", "timestamp": "2026-08-11T10:01:00+08:00"},
            ]
        )
        self.uploads.set_participant_mapping(self.owner_id, import_id, {"persona": "persona"})
        self._accept_records(import_id, {0, 1})
        builder = TrainingDatasetBuilder(self.layout, self.uploads, max_record_bytes=16)

        with self.assertRaises(TrainingDatasetError) as captured:
            builder.build(self.owner_id, self.persona.id, import_id)

        self.assertEqual("training_record_too_large", captured.exception.code)
        self.assertFalse(list((self.root / "training-source").glob("*.bin")))
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))

    def test_rejects_an_oversized_jsonl_source_row_before_dataset_write(self) -> None:
        """The parser cap protects the encrypted-import path before JSON decoding."""
        import_id = self._completed_import(
            [
                {"sender": "persona", "message": "x" * 128, "timestamp": "2026-08-11T10:00:00+08:00"},
            ]
        )
        self.uploads.set_participant_mapping(self.owner_id, import_id, {"persona": "persona"})
        builder = TrainingDatasetBuilder(self.layout, self.uploads, max_record_bytes=32)

        with self.assertRaises(TrainingDatasetError) as captured:
            builder.build(self.owner_id, self.persona.id, import_id)

        self.assertEqual("training_record_too_large", captured.exception.code)
        self.assertFalse(list((self.root / "training-source").glob("*.bin")))
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))

    def test_rejects_total_dataset_bytes_and_removes_a_partial_jsonl(self) -> None:
        """A total cap removes the first row when a later row would overflow it."""
        import_id = self._completed_import(
            [
                {"sender": "persona", "message": "样本一", "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "persona", "message": "样本二", "timestamp": "2026-08-11T10:01:00+08:00"},
            ]
        )
        self.uploads.set_participant_mapping(self.owner_id, import_id, {"persona": "persona"})
        self._accept_records(import_id, {0, 1})
        one_line_bytes = len(
            (
                json.dumps(
                    {"messages": [{"role": "assistant", "content": "样本一"}]},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )
        builder = TrainingDatasetBuilder(
            self.layout,
            self.uploads,
            max_dataset_bytes=one_line_bytes,
        )

        with self.assertRaises(TrainingDatasetError) as captured:
            builder.build(self.owner_id, self.persona.id, import_id)

        self.assertEqual("training_dataset_too_large", captured.exception.code)
        self.assertFalse(list((self.root / "training-source").glob("*.bin")))
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))

    def test_new_builder_does_not_remove_an_active_dataset_lease(self) -> None:
        """Startup cleanup must distinguish a live handoff from stale plaintext."""
        import_id = self._completed_import(
            [
                {"sender": "persona", "message": "第一条", "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "persona", "message": "第二条", "timestamp": "2026-08-11T10:01:00+08:00"},
            ]
        )
        self.uploads.set_participant_mapping(self.owner_id, import_id, {"persona": "persona"})
        self._accept_records(import_id, {0, 1})
        dataset = self.datasets.build(self.owner_id, self.persona.id, import_id)

        TrainingDatasetBuilder(self.layout, self.uploads)

        self.assertTrue(dataset.path.exists())
        dataset.cleanup()

    def test_stale_dataset_cleanup_failure_blocks_training(self) -> None:
        """A locked plaintext leftover must be visible instead of silently ignored."""
        directory = self.root / "training-datasets"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "orphan.jsonl").write_text("sensitive", encoding="utf-8")

        with patch(
            "src.services.training_dataset.PlaintextLeaseRegistry.delete_if_stale",
            side_effect=OSError("locked"),
        ):
            builder = TrainingDatasetBuilder(self.layout, self.uploads)
            with self.assertRaises(TrainingDatasetError) as captured:
                builder.build(self.owner_id, self.persona.id, "missing-import")

        self.assertEqual("training_dataset_cleanup_failed", captured.exception.code)

    def test_rejects_an_insufficient_accepted_persona_dataset_and_removes_output(self) -> None:
        import_id = self._completed_import(
            [
                {"sender": "persona", "message": "唯一已接受样本", "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "persona", "message": "未审核样本", "timestamp": "2026-08-11T10:01:00+08:00"},
            ]
        )
        self.uploads.set_participant_mapping(self.owner_id, import_id, {"persona": "persona"})
        self._accept_records(import_id, {0})

        with self.assertRaises(TrainingDatasetError) as captured:
            self.datasets.build(self.owner_id, self.persona.id, import_id)

        self.assertEqual("training_samples_insufficient", captured.exception.code)
        self.assertFalse(list((self.root / "training-source").glob("*.bin")))
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))

    def test_maps_insufficient_dataset_storage_and_removes_partial_output(self) -> None:
        import_id = self._completed_import(
            [
                {"sender": "persona", "message": "第一条", "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "persona", "message": "第二条", "timestamp": "2026-08-11T10:01:00+08:00"},
            ]
        )
        self.uploads.set_participant_mapping(self.owner_id, import_id, {"persona": "persona"})
        self._accept_records(import_id, {0, 1})

        with patch(
            "src.services.training_dataset.shutil.disk_usage",
            return_value=SimpleNamespace(free=0),
        ):
            with self.assertRaises(TrainingDatasetError) as captured:
                self.datasets.build(self.owner_id, self.persona.id, import_id)

        self.assertEqual("training_dataset_storage_unavailable", captured.exception.code)
        self.assertFalse(list((self.root / "training-source").glob("*.bin")))
        self.assertFalse(list((self.root / "training-datasets").glob("*.jsonl")))

    def test_streaming_dataset_build_does_not_block_a_different_import_upload(self) -> None:
        import_id = self._completed_import(
            [
                {"sender": "persona", "message": "第一条人物消息", "timestamp": "2026-08-11T10:00:00+08:00"},
                {"sender": "persona", "message": "第二条人物消息", "timestamp": "2026-08-11T10:01:00+08:00"},
            ]
        )
        self.uploads.set_participant_mapping(self.owner_id, import_id, {"persona": "persona"})
        preview_records = self._accept_records(import_id, {0, 1})
        started = threading.Event()
        release = threading.Event()
        failures: list[BaseException] = []
        result = {}

        def blocking_records(*_args: object, **_kwargs: object):
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("training dataset test did not release its parser")
            for index, preview_record in enumerate(preview_records):
                yield NormalizedMessage(
                    sender_id="persona",
                    sender_name="persona",
                    content=f"人物样本 {index}",
                    timestamp="2026-08-11T10:00:00+08:00",
                    message_type="text",
                    attachments=(),
                    record_id=preview_record["record_id"],
                )

        def build() -> None:
            try:
                result["dataset"] = self.datasets.build(self.owner_id, self.persona.id, import_id)
            except BaseException as exc:  # Preserve a worker failure for the assertion thread.
                failures.append(exc)

        with patch.object(self.uploads.parsers, "iter_records", side_effect=blocking_records):
            build_thread = threading.Thread(target=build)
            build_thread.start()
            self.assertTrue(started.wait(timeout=1), "training parser did not start")
            active_sources = list((self.root / "training-source").glob("*.bin"))
            self.assertEqual(1, len(active_sources))
            UploadService(
                self.layout,
                self.uploads.imports,
                self.uploads.encryption,
                read_block_bytes=8,
            )
            self.assertTrue(active_sources[0].exists())

            other_payload = b"next"
            other_job = self.uploads.imports.create(
                self.owner_id,
                self.persona.id,
                "other.txt",
                len(other_payload),
                "text/plain",
            )
            uploaded = threading.Event()

            def upload_other_import() -> None:
                try:
                    self.uploads.put_chunk(
                        self.owner_id,
                        other_job.id,
                        0,
                        len(other_payload),
                        self._digest(other_payload),
                        io.BytesIO(other_payload),
                    )
                except BaseException as exc:  # Preserve a worker failure for the assertion thread.
                    failures.append(exc)
                finally:
                    uploaded.set()

            upload_thread = threading.Thread(target=upload_other_import)
            upload_thread.start()
            completed_while_building = uploaded.wait(timeout=0.5)
            release.set()
            build_thread.join(timeout=2)
            upload_thread.join(timeout=2)

        self.assertTrue(completed_while_building, "training dataset build held the global upload lock")
        self.assertFalse(build_thread.is_alive())
        self.assertFalse(upload_thread.is_alive())
        self.assertEqual([], failures)
        result["dataset"].cleanup()


if __name__ == "__main__":
    unittest.main()
