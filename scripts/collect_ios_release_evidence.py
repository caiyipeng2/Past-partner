"""Collect redacted metadata from iOS build artifacts.

The collector is intentionally independent from Xcode so it can run after a
macOS Flutter build and produce a small, reviewable JSON artifact. It never
serializes source paths, archive contents, provisioning profiles, or signing
credentials. A successful result only proves that the supplied artifact
metadata is internally aligned; it does not claim device or store delivery.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import re
import sys
from typing import Any
import zipfile

from check_ios_release import IosReleasePolicyError, check_project


class EvidenceError(ValueError):
    """Stable failure that is safe to expose in CI logs."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_IPA_INFO_RE = re.compile(r"^Payload/[^/]+\.app/Info\.plist$")


def _read_project(root: Path) -> tuple[dict[str, str], str]:
    try:
        policy = check_project(root)
    except IosReleasePolicyError as exc:
        raise EvidenceError(exc.code, str(exc)) from exc
    return policy["version"], _read_bundle_id(root)


def _read_bundle_id(root: Path) -> str:
    plist_path = root / "mobile" / "ios" / "Runner" / "Info.plist"
    try:
        plist = plistlib.loads(plist_path.read_bytes())
    except (OSError, UnicodeError, plistlib.InvalidFileException) as exc:
        raise EvidenceError("ios_metadata_unavailable", "iOS release metadata is unavailable") from exc
    bundle_id = plist.get("CFBundleIdentifier") if isinstance(plist, dict) else None
    if not isinstance(bundle_id, str) or not bundle_id:
        raise EvidenceError("bundle_id_missing", "iOS bundle identifier is missing")
    return bundle_id


def _artifact_path(root: Path, requested: str | None, candidates: list[Path]) -> Path | None:
    if requested is not None:
        path = Path(requested)
        if not path.is_absolute():
            path = root / path
        return path.resolve() if path.exists() else None
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _load_plist(raw: bytes, invalid_code: str = "artifact_invalid") -> dict[str, Any]:
    try:
        value = plistlib.loads(raw)
    except (TypeError, ValueError, plistlib.InvalidFileException) as exc:
        raise EvidenceError(invalid_code, "iOS artifact metadata is invalid") from exc
    if not isinstance(value, dict):
        raise EvidenceError(invalid_code, "iOS artifact metadata is invalid")
    return value


def _validate_app_metadata(
    metadata: dict[str, Any], expected_version: dict[str, str], expected_bundle_id: str
) -> None:
    if (
        metadata.get("CFBundleShortVersionString") != expected_version["name"]
        or metadata.get("CFBundleVersion") != expected_version["code"]
    ):
        raise EvidenceError("artifact_version_mismatch", "iOS artifact version is not aligned")
    if metadata.get("CFBundleIdentifier") != expected_bundle_id:
        raise EvidenceError("artifact_bundle_mismatch", "iOS artifact bundle identifier is not aligned")


def _validate_archive_metadata(
    metadata: dict[str, Any], expected_version: dict[str, str], expected_bundle_id: str
) -> None:
    """Validate fields Xcode writes to archive Info.plist when present."""
    properties = metadata.get("ApplicationProperties")
    if not isinstance(properties, dict):
        raise EvidenceError("artifact_invalid", "iOS archive metadata is invalid")
    if (
        properties.get("CFBundleShortVersionString") is not None
        and properties.get("CFBundleShortVersionString") != expected_version["name"]
    ):
        raise EvidenceError("artifact_version_mismatch", "iOS artifact version is not aligned")
    if properties.get("CFBundleVersion") is not None and properties.get("CFBundleVersion") != expected_version["code"]:
        raise EvidenceError("artifact_version_mismatch", "iOS artifact version is not aligned")
    if properties.get("CFBundleIdentifier") is not None and properties.get("CFBundleIdentifier") != expected_bundle_id:
        raise EvidenceError("artifact_bundle_mismatch", "iOS artifact bundle identifier is not aligned")


def _directory_size(path: Path) -> int:
    try:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError as exc:
        raise EvidenceError("artifact_invalid", "iOS archive artifact is unavailable") from exc


def _archive_evidence(
    path: Path, expected_version: dict[str, str], expected_bundle_id: str
) -> dict[str, Any]:
    if not path.is_dir() or path.suffix != ".xcarchive":
        raise EvidenceError("artifact_invalid", "iOS archive artifact is invalid")
    info_path = path / "Info.plist"
    if not info_path.is_file():
        raise EvidenceError("artifact_invalid", "iOS archive metadata is invalid")
    _validate_archive_metadata(_load_plist(info_path.read_bytes()), expected_version, expected_bundle_id)
    apps = sorted((path / "Products" / "Applications").glob("*.app"))
    if len(apps) != 1 or not apps[0].is_dir():
        raise EvidenceError("artifact_invalid", "iOS archive application payload is invalid")
    app = apps[0]
    metadata = _load_plist((app / "Info.plist").read_bytes())
    _validate_app_metadata(metadata, expected_version, expected_bundle_id)
    return {
        "present": True,
        "size_bytes": _directory_size(path),
        "signing": "signed" if (app / "_CodeSignature" / "CodeResources").is_file() else "unsigned",
    }


def _ipa_evidence(path: Path, expected_version: dict[str, str], expected_bundle_id: str) -> dict[str, Any]:
    if not path.is_file() or path.suffix.lower() != ".ipa":
        raise EvidenceError("artifact_invalid", "iOS IPA artifact is invalid")
    try:
        with zipfile.ZipFile(path) as archive:
            info_names = [name for name in archive.namelist() if _IPA_INFO_RE.fullmatch(name)]
            if len(info_names) != 1:
                raise EvidenceError("artifact_invalid", "iOS IPA application payload is invalid")
            metadata = _load_plist(archive.read(info_names[0]))
            _validate_app_metadata(metadata, expected_version, expected_bundle_id)
            app_prefix = info_names[0][: -len("Info.plist")]
            signed = f"{app_prefix}_CodeSignature/CodeResources" in archive.namelist()
    except EvidenceError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise EvidenceError("artifact_invalid", "iOS IPA artifact is invalid") from exc
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        raise EvidenceError("artifact_invalid", "iOS IPA artifact is unavailable") from exc
    return {"present": True, "size_bytes": size_bytes, "signing": "signed" if signed else "unsigned"}


def collect_evidence(
    root: Path, archive: str | None = None, ipa: str | None = None
) -> dict[str, Any]:
    expected_version, expected_bundle_id = _read_project(root)
    archive_path = _artifact_path(
        root,
        archive,
        [root / "mobile" / "build" / "ios" / "archive" / "Runner.xcarchive"],
    )
    ipa_path = _artifact_path(
        root,
        ipa,
        sorted((root / "mobile" / "build" / "ios" / "ipa").glob("*.ipa")),
    )
    if archive_path is None and ipa_path is None:
        raise EvidenceError("artifact_missing", "iOS build artifact is missing")

    archive_evidence = (
        _archive_evidence(archive_path, expected_version, expected_bundle_id)
        if archive_path is not None
        else {"present": False}
    )
    ipa_evidence = (
        _ipa_evidence(ipa_path, expected_version, expected_bundle_id)
        if ipa_path is not None
        else {"present": False}
    )
    return {
        "status": "ok",
        "version": expected_version,
        "bundle_id": expected_bundle_id,
        "artifacts": {"archive": archive_evidence, "ipa": ipa_evidence},
        "checks": {
            "version_alignment": True,
            "bundle_alignment": True,
            "metadata_redacted": True,
        },
    }


def _emit(payload: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if output is not None:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            raise EvidenceError("output_unavailable", "iOS evidence output is unavailable") from exc
    print(rendered)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--archive", type=str)
    parser.add_argument("--ipa", type=str)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = collect_evidence(args.project_root.resolve(), args.archive, args.ipa)
        _emit(payload, args.output)
    except EvidenceError as exc:
        error_payload = {"status": "error", "code": exc.code, "message": str(exc)}
        try:
            _emit(error_payload, args.output)
        except EvidenceError:
            # Keep the stable JSON error available on stdout even when the
            # requested output directory is not writable on the runner.
            _emit(error_payload, None)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
