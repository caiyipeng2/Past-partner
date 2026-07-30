"""Validated runtime configuration loaded at the process boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from src.services.import_service import DEFAULT_MAX_IMPORT_BYTES
from src.services.upload_service import DEFAULT_CHUNK_BYTES


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: Path = Path("data/runtime")
    web_dir: Path = Path("web")
    mode: str = "development"
    cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    )
    max_json_bytes: int = 1024 * 1024
    max_chunk_bytes: int = DEFAULT_CHUNK_BYTES
    max_import_bytes: int = DEFAULT_MAX_IMPORT_BYTES

    @classmethod
    def from_env(cls) -> "ServerConfig":
        default = cls()
        origins = os.getenv("PAST_PARTNER_CORS_ORIGINS")
        return cls(
            host=os.getenv("PAST_PARTNER_HOST", default.host),
            port=_int_env("PAST_PARTNER_PORT", default.port),
            data_dir=Path(os.getenv("PAST_PARTNER_DATA_DIR", str(default.data_dir))),
            web_dir=Path(os.getenv("PAST_PARTNER_WEB_DIR", str(default.web_dir))),
            mode=os.getenv("PAST_PARTNER_MODE", default.mode),
            cors_origins=tuple(item.strip() for item in origins.split(",") if item.strip()) if origins else default.cors_origins,
            max_json_bytes=_int_env("PAST_PARTNER_MAX_JSON_BYTES", default.max_json_bytes),
            max_chunk_bytes=_int_env("PAST_PARTNER_MAX_CHUNK_BYTES", default.max_chunk_bytes),
            max_import_bytes=_int_env("PAST_PARTNER_MAX_IMPORT_BYTES", default.max_import_bytes),
        ).validated()

    def validated(self) -> "ServerConfig":
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if self.mode not in {"development", "test", "production"}:
            raise ValueError("mode must be development, test, or production")
        if min(self.max_json_bytes, self.max_chunk_bytes, self.max_import_bytes) <= 0:
            raise ValueError("request and import limits must be positive")
        return replace(
            self,
            data_dir=self.data_dir.expanduser().resolve(),
            web_dir=self.web_dir.expanduser().resolve(),
        )


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
