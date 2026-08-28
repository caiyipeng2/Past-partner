"""Validate the iOS release source policy without requiring macOS or Xcode."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import re
import sys
from typing import Any


_VERSION = re.compile(r"(?m)^\s*version:\s*([^\s]+)\s*$")
_FORBIDDEN_ATS_KEYS = frozenset(
    {"NSAppTransportSecurity", "NSAllowsArbitraryLoads", "NSExceptionDomains"}
)


class IosReleasePolicyError(ValueError):
    """Stable validation failure that does not include local paths or secrets."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def check_project(root: Path) -> dict[str, Any]:
    mobile = root / "mobile"
    pubspec = mobile / "pubspec.yaml"
    plist_path = mobile / "ios" / "Runner" / "Info.plist"
    try:
        version_text = pubspec.read_text(encoding="utf-8")
        version_match = _VERSION.search(version_text)
        if version_match is None:
            raise IosReleasePolicyError("version_missing", "mobile version is missing")
        raw_version = version_match.group(1)
        if raw_version.count("+") != 1:
            raise IosReleasePolicyError("version_invalid", "mobile version must contain name and code")
        build_name, build_code = raw_version.split("+", 1)
        if not build_name or not build_code:
            raise IosReleasePolicyError("version_invalid", "mobile version must contain name and code")
        plist = plistlib.loads(plist_path.read_bytes())
    except IosReleasePolicyError:
        raise
    except (OSError, UnicodeError, plistlib.InvalidFileException) as exc:
        raise IosReleasePolicyError("ios_metadata_unavailable", "iOS release metadata is unavailable") from exc

    if not isinstance(plist, dict):
        raise IosReleasePolicyError("plist_invalid", "iOS release metadata is invalid")
    if _contains_forbidden_ats_key(plist):
        raise IosReleasePolicyError("ats_exception_present", "iOS release transport policy is not fail-closed")
    if plist.get("CFBundleShortVersionString") != build_name or plist.get("CFBundleVersion") != build_code:
        raise IosReleasePolicyError("version_mismatch", "iOS and Flutter versions are not aligned")
    return {
        "status": "ok",
        "version": {"name": build_name, "code": build_code},
        "checks": {"ats_exceptions": False, "version_alignment": True},
    }


def _contains_forbidden_ats_key(value: object) -> bool:
    if isinstance(value, dict):
        if any(key in _FORBIDDEN_ATS_KEYS for key in value):
            return True
        return any(_contains_forbidden_ats_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_forbidden_ats_key(item) for item in value)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        payload = check_project(args.project_root.resolve())
    except IosReleasePolicyError as exc:
        print(json.dumps({"status": "error", "code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
