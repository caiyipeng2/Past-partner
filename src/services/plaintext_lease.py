"""Process-wide activity tracking for short-lived plaintext training artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import threading


class PlaintextLeaseRegistry:
    """Prevent one local service instance from deleting another live handoff file.

    Training source and dataset files are intentionally plaintext for the brief
    parser/provider boundary. A new local application instance cleans leftovers from
    a prior process, while a second builder in the current process must leave an
    active file alone. Every check and filesystem mutation shares this lock so a
    rename cannot be observed as an unregistered stale file.
    """

    _lock = threading.RLock()
    _active_paths: set[Path] = set()

    @classmethod
    def reserve(cls, path: Path) -> None:
        with cls._lock:
            cls._active_paths.add(cls._key(path))

    @classmethod
    def promote(cls, source: Path, destination: Path) -> None:
        """Atomically rename an active temporary file and retain its lease."""
        with cls._lock:
            os.replace(source, destination)
            cls._active_paths.discard(cls._key(source))
            cls._active_paths.add(cls._key(destination))

    @classmethod
    def delete_and_release(cls, path: Path) -> None:
        """Delete a lease file; retain registration if removal fails for visibility."""
        with cls._lock:
            path.unlink(missing_ok=True)
            cls._active_paths.discard(cls._key(path))

    @classmethod
    def abandon(cls, path: Path) -> None:
        """Release a reserved path that was never successfully materialized."""
        with cls._lock:
            cls._active_paths.discard(cls._key(path))

    @classmethod
    def delete_if_stale(cls, path: Path) -> bool:
        """Remove a startup leftover only when no current process lease owns it."""
        with cls._lock:
            if cls._key(path) in cls._active_paths:
                return False
            path.unlink(missing_ok=True)
            return True

    @staticmethod
    def _key(path: Path) -> Path:
        return path.resolve(strict=False)
