import base64
import shutil
import sqlite3
import unittest
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from dataclasses import replace
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.import_repository import ImportRepository, ImportRepositoryError
from src.services.import_service import ImportFile, ImportJob, ImportState
from src.services.local_auth import LocalAuthService
from src.services.metadata_store import MetadataIntegrityError
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR, EnvironmentMasterKeyProvider
from src.services.storage import StorageLayout


class ImportRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.layout = StorageLayout(self.root)
        key = base64.b64encode(b"m" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.repository = ImportRepository(self.layout.database_path(), self.encryption)
        self.auth = LocalAuthService(self.layout.database_path(), self.encryption, mode="test")
        self.job = self._job()
        self.manifest = {
            "version": 2,
            "import_id": self.job.id,
            "chunks": {
                "0": {
                    "length": 5,
                    "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
                    "encrypted_length": 33,
                }
            },
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _job(self) -> ImportJob:
        now = datetime.now(UTC).isoformat()
        return ImportJob(
            id=str(uuid4()),
            persona_id=str(uuid4()),
            source_name="聊天记录.zip",
            media_type="application/zip",
            total_bytes=5,
            received_bytes=5,
            chunk_count=1,
            state=ImportState.UPLOADING,
            created_at=now,
            updated_at=now,
        )

    def test_job_and_manifest_are_encrypted_and_round_trip(self) -> None:
        self.repository.create(self.job, self.manifest)

        self.assertEqual(self.job, self.repository.get(self.job.id))
        self.assertEqual(self.manifest, self.repository.get_manifest(self.job.id))
        self.assertFalse((self.root / "imports").exists())
        self.assertFalse((self.root / "upload-manifests").exists())
        database_bytes = self.layout.database_path().read_bytes()
        self.assertNotIn("聊天记录.zip".encode("utf-8"), database_bytes)
        self.assertNotIn(b"2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824", database_bytes)

        reopened = ImportRepository(self.layout.database_path(), self.encryption)
        self.assertEqual(self.job, reopened.get(self.job.id))
        self.assertEqual(self.manifest, reopened.get_manifest(self.job.id))

    def test_multi_file_manifest_is_persisted_and_encrypted(self) -> None:
        multi_file_job = replace(
            self.job,
            total_bytes=12,
            source_name="wechat.txt",
            media_type="text/plain",
            files=(
                ImportFile("file-a", "wechat.txt", "text/plain", 5, "a" * 64),
                ImportFile("file-b", "photo.jpg", "image/jpeg", 7, None),
            ),
        )

        self.repository.create(multi_file_job)

        stored = self.repository.get_manifest(multi_file_job.id)
        self.assertIsNotNone(stored)
        self.assertEqual(
            ["file-a", "file-b"],
            [item["file_id"] for item in stored["files"]],
        )
        self.assertEqual(multi_file_job, self.repository.get(multi_file_job.id))
        database_bytes = self.layout.database_path().read_bytes()
        self.assertNotIn(b"wechat.txt", database_bytes)
        self.assertNotIn(b"photo.jpg", database_bytes)

    def test_tampered_job_and_manifest_fail_closed(self) -> None:
        self.repository.create(self.job, self.manifest)
        with closing(sqlite3.connect(self.layout.database_path())) as connection:
            connection.execute(
                "UPDATE imports SET encrypted_payload = ? WHERE id = ?",
                (sqlite3.Binary(b"tampered"), self.job.id),
            )
            connection.commit()
        with self.assertRaises(ImportRepositoryError) as captured_job:
            self.repository.get(self.job.id)
        self.assertEqual("import_record_authentication_failed", captured_job.exception.code)

        manifest_database = self.root / "manifest-only.sqlite3"
        manifest_repository = ImportRepository(manifest_database, self.encryption)
        manifest_repository.create(self.job, self.manifest)
        with closing(sqlite3.connect(manifest_database)) as connection:
            connection.execute(
                "UPDATE import_manifests SET encrypted_payload = ? WHERE import_id = ?",
                (sqlite3.Binary(b"tampered"), self.job.id),
            )
            connection.commit()
        with self.assertRaises(ImportRepositoryError) as captured_manifest:
            manifest_repository.get_manifest(self.job.id)
        self.assertEqual("manifest_record_authentication_failed", captured_manifest.exception.code)

    def test_state_transaction_rolls_back_job_when_manifest_update_fails(self) -> None:
        self.repository.create(self.job, self.manifest)
        changed = replace(
            self.job,
            source_name="changed.zip",
            updated_at=datetime.now(UTC).isoformat(),
        )
        changed_manifest = {**self.manifest, "final_encrypted_length": 32}
        with closing(sqlite3.connect(self.layout.database_path())) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_manifest_update
                BEFORE UPDATE ON import_manifests
                BEGIN SELECT RAISE(ABORT, 'blocked'); END
                """
            )
            connection.commit()

        with self.assertRaises(MetadataIntegrityError):
            self.repository.save_state(changed, changed_manifest)

        self.assertEqual(self.job, self.repository.get(self.job.id))
        self.assertEqual(self.manifest, self.repository.get_manifest(self.job.id))

    def test_migrates_legacy_json_and_removes_plaintext_after_commit(self) -> None:
        imports_dir = self.root / "imports"
        manifests_dir = self.root / "upload-manifests"
        job_path = self.layout.write_json("imports", self.job.id, self.job.to_dict())
        manifest_path = self.layout.write_json("upload-manifests", self.job.id, self.manifest)

        self.assertEqual(1, self.repository.migrate_legacy_json(imports_dir, manifests_dir))
        self.assertFalse(job_path.exists())
        self.assertFalse(manifest_path.exists())
        self.assertEqual(self.job, self.repository.get(self.job.id))
        self.assertEqual(self.manifest, self.repository.get_manifest(self.job.id))
        self.assertNotIn("聊天记录.zip".encode("utf-8"), self.layout.database_path().read_bytes())

    def test_failed_legacy_migration_preserves_plaintext_sources(self) -> None:
        job_path = self.layout.write_json("imports", self.job.id, self.job.to_dict())
        manifest_path = self.layout.write_json(
            "upload-manifests", self.job.id, {"version": 1, "import_id": self.job.id, "chunks": {}}
        )

        with self.assertRaises(ImportRepositoryError) as captured:
            self.repository.migrate_legacy_json(self.root / "imports", self.root / "upload-manifests")

        self.assertEqual("legacy_manifest_record_invalid", captured.exception.code)
        self.assertTrue(job_path.exists())
        self.assertTrue(manifest_path.exists())
        self.assertIsNone(self.repository.get(self.job.id))

    def test_import_and_manifest_are_scoped_to_the_owner_id(self) -> None:
        self.repository.create(self.auth.owner_id, self.job, self.manifest)

        self.assertEqual(self.job, self.repository.get(self.auth.owner_id, self.job.id))
        self.assertEqual(self.manifest, self.repository.get_manifest(self.auth.owner_id, self.job.id))
        self.assertIsNone(self.repository.get("other-owner", self.job.id))
        self.assertIsNone(self.repository.get_manifest("other-owner", self.job.id))

    def test_lists_imports_for_a_persona_with_owner_scope(self) -> None:
        other_persona_job = replace(self.job, id=str(uuid4()), persona_id=str(uuid4()))
        self.repository.create(self.auth.owner_id, self.job, self.manifest)
        self.repository.create(self.auth.owner_id, other_persona_job, self.manifest | {"import_id": other_persona_job.id})

        self.assertEqual(
            [self.job],
            self.repository.list_for_persona(self.auth.owner_id, self.job.persona_id),
        )
        self.assertEqual([], self.repository.list_for_persona("other-owner", self.job.persona_id))

    def test_lists_all_imports_for_a_requested_owner(self) -> None:
        other_job = replace(self.job, id=str(uuid4()), persona_id=str(uuid4()))
        self.repository.create(self.auth.owner_id, self.job, self.manifest)
        self.repository.create(
            self.auth.owner_id,
            other_job,
            self.manifest | {"import_id": other_job.id},
        )

        self.assertEqual([self.job, other_job], self.repository.list(self.auth.owner_id))
        self.assertEqual([], self.repository.list("other-owner"))

    def test_lists_only_expired_terminal_imports_for_an_owner(self) -> None:
        now = datetime.now(UTC)
        cutoff = now.replace(microsecond=0)
        old = replace(
            self.job,
            state=ImportState.CANCELLED,
            updated_at=(cutoff.replace(year=cutoff.year - 1)).isoformat(),
        )
        failed = replace(
            self.job,
            id=str(uuid4()),
            state=ImportState.FAILED,
            updated_at=(cutoff.replace(year=cutoff.year - 1)).isoformat(),
        )
        active = replace(
            self.job,
            id=str(uuid4()),
            state=ImportState.UPLOADING,
            updated_at=(cutoff.replace(year=cutoff.year - 1)).isoformat(),
        )
        newer_cancelled = replace(
            self.job,
            id=str(uuid4()),
            state=ImportState.CANCELLED,
            updated_at=(cutoff.replace(year=cutoff.year + 1)).isoformat(),
        )
        for job in (old, failed, active, newer_cancelled):
            self.repository.create(self.auth.owner_id, job)

        expired = self.repository.list_expired_terminal(self.auth.owner_id, cutoff)

        self.assertEqual([old.id, failed.id], [job.id for job in expired])


if __name__ == "__main__":
    unittest.main()
