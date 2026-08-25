import json
import shutil
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from src.services.export_service import ExportService
from src.services.storage import StorageLayout


class _Imports:
    def __init__(self):
        self.jobs = [SimpleNamespace(id="import-1", to_dict=lambda: {"id": "import-1", "state": "uploaded"})]

    def list(self, owner_id):
        return list(self.jobs)

    def get_manifest(self, owner_id, import_id):
        return {"version": 2, "import_id": import_id, "chunks": {"0": {"length": 11}}}


class _Uploads:
    def iter_payload(self, owner_id, import_id):
        yield b"hello "
        yield b"world"


class ExportServiceTests(unittest.TestCase):
    def test_archive_contains_metadata_and_streamed_raw_payload(self):
        with TemporaryDirectory() as root:
            layout = StorageLayout(Path(root))
            service = ExportService(layout, _Imports(), _Uploads())
            artifact = service.create_archive("owner-1", {"export_version": 2, "scope": {"raw_payloads_included": True}})
            try:
                with zipfile.ZipFile(artifact.path) as archive:
                    self.assertEqual(
                        {"manifest.json", "imports/import-1/job.json", "imports/import-1/manifest.json", "imports/import-1/payload.bin"},
                        set(archive.namelist()),
                    )
                    self.assertEqual(b"hello world", archive.read("imports/import-1/payload.bin"))
                    manifest = json.loads(archive.read("manifest.json"))
                    self.assertTrue(manifest["scope"]["raw_payloads_included"])
            finally:
                artifact.cleanup()
            self.assertFalse(artifact.path.exists())


if __name__ == "__main__":
    unittest.main()
