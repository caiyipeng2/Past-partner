import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from src.services.retention_service import RetentionService


class _Imports:
    def __init__(self, jobs, normalized_jobs=None):
        self.jobs = jobs
        self.normalized_jobs = normalized_jobs or []
        self.calls = []
        self.normalized_calls = []

    def list_expired_terminal(self, owner_id, cutoff):
        self.calls.append((owner_id, cutoff))
        return list(self.jobs)

    def list_expired_normalized(self, owner_id, cutoff):
        self.normalized_calls.append((owner_id, cutoff))
        return list(self.normalized_jobs)


class _Uploads:
    def __init__(self):
        self.deleted = []

    def delete_import(self, owner_id, import_id):
        self.deleted.append((owner_id, import_id))
        return {"import_id": import_id, "deleted": True}


class RetentionServiceTests(unittest.TestCase):
    def test_disabled_retention_does_not_scan_or_delete(self) -> None:
        imports = _Imports([SimpleNamespace(id="old")])
        uploads = _Uploads()
        service = RetentionService(imports, uploads, retention_seconds=0)

        result = service.cleanup("owner-1", now=datetime(2026, 8, 6, tzinfo=UTC))

        self.assertFalse(result["enabled"])
        self.assertEqual([], imports.calls)
        self.assertEqual([], uploads.deleted)

    def test_cleanup_deletes_each_expired_terminal_import_for_owner(self) -> None:
        imports = _Imports([SimpleNamespace(id="old-a"), SimpleNamespace(id="old-b")])
        uploads = _Uploads()
        service = RetentionService(imports, uploads, retention_seconds=3600)
        now = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

        result = service.cleanup("owner-1", now=now)

        self.assertTrue(result["enabled"])
        self.assertEqual(2, result["deleted_count"])
        self.assertEqual(["old-a", "old-b"], result["deleted_import_ids"])
        self.assertEqual([("owner-1", "old-a"), ("owner-1", "old-b")], uploads.deleted)
        self.assertEqual(now - timedelta(seconds=3600), imports.calls[0][1])

    def test_cleanup_deletes_expired_successfully_normalized_imports(self) -> None:
        imports = _Imports([], normalized_jobs=[SimpleNamespace(id="normalized-old")])
        uploads = _Uploads()
        service = RetentionService(
            imports,
            uploads,
            retention_seconds=0,
            normalized_retention_seconds=3600,
        )
        now = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

        result = service.cleanup("owner-1", now=now)

        self.assertTrue(result["normalized_enabled"])
        self.assertEqual(1, result["normalized_deleted_count"])
        self.assertEqual(["normalized-old"], result["normalized_deleted_import_ids"])
        self.assertEqual([("owner-1", "normalized-old")], uploads.deleted)
        self.assertEqual(now - timedelta(seconds=3600), imports.normalized_calls[0][1])

    def test_normalized_only_retention_does_not_scan_terminal_raw_imports(self) -> None:
        imports = _Imports([], normalized_jobs=[])
        uploads = _Uploads()
        RetentionService(
            imports,
            uploads,
            retention_seconds=0,
            normalized_retention_seconds=3600,
        ).cleanup("owner-1", now=datetime(2026, 8, 6, 12, 0, tzinfo=UTC))

        self.assertEqual([], imports.calls)

    def test_retention_rejects_values_above_policy_maximum(self) -> None:
        with self.assertRaisesRegex(ValueError, "retention"):
            RetentionService(_Imports([]), _Uploads(), retention_seconds=RetentionService.MAX_RETENTION_SECONDS + 1)


if __name__ == "__main__":
    unittest.main()
