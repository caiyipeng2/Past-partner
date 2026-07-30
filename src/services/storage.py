"""Filesystem layout that never trusts client-controlled path fragments."""

from __future__ import annotations

import re
import json
import os
from pathlib import Path, PureWindowsPath
from typing import Any
from uuid import uuid4


class InvalidStorageIdentifier(ValueError):
    """Raised before unsafe text can participate in filesystem resolution."""


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SUFFIX = re.compile(r"^(?:\.[A-Za-z0-9]{1,16})?$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class StorageLayout:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()

    def object_path(self, collection: str, object_id: str, suffix: str = "") -> Path:
        safe_collection = self._validate_identifier(collection, "collection")
        safe_object_id = self._validate_identifier(object_id, "object_id")
        if not isinstance(suffix, str) or not _SUFFIX.fullmatch(suffix):
            raise InvalidStorageIdentifier("suffix must be empty or a simple extension")

        candidate = (self.root / safe_collection / f"{safe_object_id}{suffix}").resolve()
        # Validation above is the primary guard. This containment check also protects
        # future changes to the accepted identifier grammar.
        if candidate != self.root and self.root not in candidate.parents:
            raise InvalidStorageIdentifier("resolved path escapes the storage root")
        return candidate

    def ensure_collection(self, collection: str) -> Path:
        sentinel = self.object_path(collection, "sentinel")
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        return sentinel.parent

    def write_json(self, collection: str, object_id: str, value: Any) -> Path:
        destination = self.object_path(collection, object_id, ".json")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def read_json(self, collection: str, object_id: str) -> Any:
        source = self.object_path(collection, object_id, ".json")
        with source.open("r", encoding="utf-8") as stream:
            return json.load(stream)

    @staticmethod
    def _validate_identifier(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise InvalidStorageIdentifier(f"{field_name} contains unsafe characters")
        if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
            raise InvalidStorageIdentifier(f"{field_name} must be relative")
        if any(ord(character) < 32 for character in value):
            raise InvalidStorageIdentifier(f"{field_name} contains control characters")
        # Windows normalizes trailing dots and reserves these basenames even when
        # an extension is present. Reject them before adding our own suffix.
        if value.endswith(".") or value.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
            raise InvalidStorageIdentifier(f"{field_name} is reserved by the filesystem")
        return value
