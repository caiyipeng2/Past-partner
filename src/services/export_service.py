"""Owner-scoped, disk-backed archive export without buffering raw imports."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping
import zipfile

from src.services.storage import StorageLayout


class ExportServiceError(RuntimeError):
    """Stable export failure that does not expose storage or payload details."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ExportArtifact:
    path: Path
    content_length: int
    sha256: str

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)


class ExportService:
    """Build a ZIP in the configured data directory and expose it as a file."""

    _BLOCK_BYTES = 1024 * 1024

    def __init__(self, storage: StorageLayout, imports: Any, uploads: Any):
        self.storage = storage
        self.imports = imports
        self.uploads = uploads

    def create_archive(self, owner_id: str, metadata: Mapping[str, Any]) -> ExportArtifact:
        if not isinstance(owner_id, str) or not owner_id:
            raise ExportServiceError("export_owner_invalid", "export owner is invalid")
        export_dir = self.storage.ensure_collection("exports")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix="owner-export-",
                suffix=".zip",
                dir=export_dir,
                delete=False,
            ) as output:
                temporary = Path(output.name)
                with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    manifest = json.dumps(
                        dict(metadata), ensure_ascii=False, separators=(",", ":"), sort_keys=True
                    ).encode("utf-8")
                    archive.writestr("manifest.json", manifest)
                    for item in self.imports.list(owner_id):
                        import_id = str(item.id)
                        job = item.to_dict()
                        import_manifest = self.imports.get_manifest(owner_id, import_id) or {}
                        archive.writestr(
                            f"imports/{import_id}/job.json",
                            json.dumps(job, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
                        )
                        archive.writestr(
                            f"imports/{import_id}/manifest.json",
                            json.dumps(import_manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
                        )
                        with archive.open(f"imports/{import_id}/payload.bin", "w", force_zip64=True) as target:
                            try:
                                for block in self.uploads.iter_payload(owner_id, import_id):
                                    if not isinstance(block, bytes):
                                        raise ExportServiceError("export_payload_invalid", "export payload is invalid")
                                    target.write(block)
                            except ExportServiceError:
                                raise
                            except Exception as exc:
                                raise ExportServiceError(
                                    "export_payload_unavailable",
                                    "a complete raw import payload is unavailable",
                                ) from exc
                output.flush()
        except ExportServiceError:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise ExportServiceError("export_failed", "owner export could not be created") from exc

        if temporary is None:
            raise ExportServiceError("export_failed", "owner export could not be created")
        try:
            content_length = temporary.stat().st_size
            digest = hashlib.sha256()
            with temporary.open("rb") as source:
                while block := source.read(self._BLOCK_BYTES):
                    digest.update(block)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ExportServiceError("export_failed", "owner export could not be verified") from exc
        return ExportArtifact(temporary, content_length, digest.hexdigest())


__all__ = ["ExportArtifact", "ExportService", "ExportServiceError"]
