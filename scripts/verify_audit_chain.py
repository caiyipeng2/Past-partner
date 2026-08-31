"""Verify the local redacted audit hash chain without exposing private data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from src.services.audit_repository import AuditRepository, AuditRepositoryError


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, help="SQLite metadata database file")
    parser.add_argument("--owner", help="verify one owner; omit to verify every owner")
    args = parser.parse_args(argv)

    database_path = Path(args.database).expanduser()
    if not database_path.is_file():
        _emit_failure("audit_database_not_found")
        return 1
    try:
        result = AuditRepository.verify_database(database_path, args.owner)
    except AuditRepositoryError as exc:
        _emit_failure(exc.code)
        return 1
    except Exception:
        _emit_failure("audit_unavailable")
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, separators=(",", ":")))
    return 0


def _emit_failure(code: str) -> None:
    print(json.dumps({"ok": False, "error": {"code": code}}, separators=(",", ":")))


if __name__ == "__main__":
    raise SystemExit(main())
