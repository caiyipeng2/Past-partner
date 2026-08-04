"""Local owner sessions for the loopback development service."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.database import SQLiteMigrator


class LocalAuthError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class OwnerPrincipal:
    user_id: str


class LocalAuthService:
    """Creates one local owner and authenticates short-lived bearer sessions."""

    _USER_RECORD_VERSION = 1
    _USER_AAD_PREFIX = "past-partner/local-user/v1/"
    _DEFAULT_SESSION_TTL = timedelta(hours=24)

    def __init__(
        self,
        database_path: Path | str,
        encryption: AuthenticatedEncryptionService,
        *,
        mode: str = "development",
        bootstrap_token: str | None = None,
        session_ttl: timedelta | None = None,
    ) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.encryption = encryption
        self.mode = mode
        self.bootstrap_token = bootstrap_token
        self.session_ttl = session_ttl or self._DEFAULT_SESSION_TTL
        if self.session_ttl <= timedelta(0):
            raise ValueError("session_ttl must be positive")
        SQLiteMigrator(self.database_path).migrate()
        self.owner_id = self._ensure_owner()

    def issue_session(self, remote_address: str, presented_bootstrap_token: str | None = None) -> dict[str, str]:
        if self.mode in {"development", "test"}:
            if remote_address not in {"127.0.0.1", "::1", "localhost"}:
                raise LocalAuthError("auth_bootstrap_forbidden", "local sessions require a loopback client")
        elif not self.bootstrap_token or not presented_bootstrap_token or not hmac.compare_digest(
            self.bootstrap_token, presented_bootstrap_token
        ):
            raise LocalAuthError("auth_bootstrap_required", "owner bootstrap token is required")

        issued_at = datetime.now(UTC)
        expires_at = issued_at + self.session_ttl
        token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(token)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM local_sessions WHERE expires_at <= ?",
                (issued_at.isoformat(),),
            )
            connection.execute(
                "INSERT INTO local_sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (sqlite3.Binary(token_hash), self.owner_id, expires_at.isoformat()),
            )
            connection.commit()
        return {
            "access_token": token,
            "token_type": "Bearer",
            "owner_id": self.owner_id,
            "expires_at": expires_at.isoformat(),
        }

    def authenticate(self, authorization: str | None) -> OwnerPrincipal:
        token = self._parse_bearer(authorization)
        token_hash = self._token_hash(token)
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id, expires_at FROM local_sessions WHERE token_hash = ?",
                (sqlite3.Binary(token_hash),),
            ).fetchone()
        if row is None or row[0] != self.owner_id or str(row[1]) <= now:
            raise LocalAuthError("authentication_required", "a valid owner session is required")
        return OwnerPrincipal(str(row[0]))

    def _ensure_owner(self) -> str:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id, record_version, encrypted_payload FROM local_users WHERE kind = 'owner'"
            ).fetchone()
            if row is not None:
                owner_id = str(row[0])
                self._decode_owner(owner_id, row[1], row[2])
                connection.commit()
                return owner_id

            owner_id = secrets.token_hex(16)
            payload = json.dumps(
                {"id": owner_id, "role": "owner", "created_at": datetime.now(UTC).isoformat()},
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            envelope = self.encryption.encrypt(payload, self._aad(owner_id))
            connection.execute(
                """
                INSERT INTO local_users (id, kind, record_version, encrypted_payload)
                VALUES (?, 'owner', ?, ?)
                """,
                (owner_id, self._USER_RECORD_VERSION, envelope),
            )
            connection.commit()
            return owner_id

    def _decode_owner(self, owner_id: str, record_version: object, envelope: object) -> None:
        if record_version != self._USER_RECORD_VERSION or not isinstance(envelope, bytes):
            raise LocalAuthError("auth_owner_record_unsupported", "owner record version is unsupported")
        try:
            payload = self.encryption.decrypt(envelope, self._aad(owner_id))
            value = json.loads(payload.decode("utf-8"))
        except (AuthenticationError, InvalidEncryptedPayloadError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalAuthError("auth_owner_record_invalid", "owner record authentication failed") from exc
        if not isinstance(value, dict) or value.get("id") != owner_id or value.get("role") != "owner":
            raise LocalAuthError("auth_owner_record_invalid", "owner record is invalid")

    @staticmethod
    def _parse_bearer(authorization: str | None) -> str:
        if not isinstance(authorization, str):
            raise LocalAuthError("authentication_required", "a valid owner session is required")
        scheme, separator, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not separator or not token or len(token) > 256:
            raise LocalAuthError("authentication_required", "a valid owner session is required")
        return token

    @staticmethod
    def _token_hash(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    @classmethod
    def _aad(cls, owner_id: str) -> bytes:
        return f"{cls._USER_AAD_PREFIX}{owner_id}".encode("utf-8")

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
