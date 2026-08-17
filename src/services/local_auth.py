"""Local owner sessions for the loopback development service."""

from __future__ import annotations

import hashlib
import hmac
import json
import binascii
import base64
import ipaddress
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.services.metadata_store import MetadataStore, require_metadata_store

if TYPE_CHECKING:
    from src.server.config import DevicePairingSettings


class LocalAuthError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class OwnerPrincipal:
    user_id: str


class PairingAttemptLimiter:
    """Bounds failed device bootstrap attempts inside one service process."""

    _FAILURE_WINDOW = 600.0
    _GLOBAL_WINDOW = 60.0
    _MAX_FAILURES_PER_PEER = 5
    _MAX_ATTEMPTS_GLOBAL = 20

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._attempts: deque[float] = deque()

    def allow(self, peer: str) -> bool:
        now = self._clock()
        with self._lock:
            self._prune(now, peer)
            if len(self._attempts) >= self._MAX_ATTEMPTS_GLOBAL:
                return False
            if len(self._failures[peer]) >= self._MAX_FAILURES_PER_PEER:
                return False
            self._attempts.append(now)
            return True

    def record_failure(self, peer: str) -> None:
        now = self._clock()
        with self._lock:
            self._prune(now, peer)
            self._failures[peer].append(now)

    def _prune(self, now: float, peer: str) -> None:
        while self._attempts and now - self._attempts[0] >= self._GLOBAL_WINDOW:
            self._attempts.popleft()
        failures = self._failures[peer]
        while failures and now - failures[0] >= self._FAILURE_WINDOW:
            failures.popleft()


class LocalAuthService:
    """Creates one local owner and authenticates short-lived bearer sessions."""

    _USER_RECORD_VERSION = 1
    _USER_AAD_PREFIX = "past-partner/local-user/v1/"
    _DEFAULT_SESSION_TTL = timedelta(hours=24)

    def __init__(
        self,
        database_path: Path | str | MetadataStore,
        encryption: AuthenticatedEncryptionService,
        *,
        mode: str = "development",
        bootstrap_token: str | None = None,
        session_ttl: timedelta | None = None,
        device_pairing: DevicePairingSettings | None = None,
        monotonic_clock=time.monotonic,
    ) -> None:
        self.metadata_store = require_metadata_store(database_path)
        self.database_path = getattr(self.metadata_store, "database_path", None)
        self.encryption = encryption
        self.mode = mode
        self.bootstrap_token = bootstrap_token
        self.device_pairing = device_pairing
        self._pairing_limiter = PairingAttemptLimiter(monotonic_clock or time.monotonic)
        self.session_ttl = session_ttl or self._DEFAULT_SESSION_TTL
        if self.session_ttl <= timedelta(0):
            raise ValueError("session_ttl must be positive")
        self.metadata_store.migrate()
        self.owner_id = self._ensure_owner()

    def issue_session(
        self,
        remote_address: str,
        presented_bootstrap_token: str | None = None,
        presented_device_bootstrap_token: str | bytes | None = None,
    ) -> dict[str, str]:
        session_origin = "loopback"
        pairing_fingerprint: bytes | None = None
        if self.mode in {"development", "test"}:
            if remote_address in {"127.0.0.1", "::1", "localhost"}:
                pass
            else:
                session_origin, pairing_fingerprint = self._authorize_device_pairing(
                    remote_address, presented_device_bootstrap_token
                )
        elif not self.bootstrap_token or not presented_bootstrap_token or not hmac.compare_digest(
            self.bootstrap_token, presented_bootstrap_token
        ):
            raise LocalAuthError("auth_bootstrap_required", "owner bootstrap token is required")

        issued_at = datetime.now(UTC)
        ttl = min(self.session_ttl, timedelta(hours=1)) if session_origin == "device" else self.session_ttl
        expires_at = issued_at + ttl
        token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(token)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM local_sessions WHERE expires_at <= ?",
                (issued_at.isoformat(),),
            )
            connection.execute(
                """
                INSERT INTO local_sessions
                    (token_hash, user_id, expires_at, session_origin, pairing_token_fingerprint)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    sqlite3.Binary(token_hash),
                    self.owner_id,
                    expires_at.isoformat(),
                    session_origin,
                    sqlite3.Binary(pairing_fingerprint) if pairing_fingerprint else None,
                ),
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
                "SELECT user_id, expires_at, session_origin, pairing_token_fingerprint FROM local_sessions WHERE token_hash = ?",
                (sqlite3.Binary(token_hash),),
            ).fetchone()
        if row is None or row[0] != self.owner_id or str(row[1]) <= now:
            raise LocalAuthError("authentication_required", "a valid owner session is required")
        if row[2] not in {"loopback", "device"}:
            raise LocalAuthError("authentication_required", "a valid owner session is required")
        if row[2] == "device":
            current = self.device_pairing
            raw_stored = row[3]
            stored = bytes(raw_stored) if isinstance(raw_stored, (bytes, bytearray, memoryview)) else b""
            if self.mode != "development" or current is None or len(stored) != hashlib.sha256().digest_size:
                raise LocalAuthError("authentication_required", "a valid owner session is required")
            if not hmac.compare_digest(stored, current.token_fingerprint):
                raise LocalAuthError("authentication_required", "a valid owner session is required")
        return OwnerPrincipal(str(row[0]))

    def _authorize_device_pairing(
        self,
        remote_address: str,
        presented_token: str | bytes | None,
    ) -> tuple[str, bytes]:
        settings = self.device_pairing
        if self.mode != "development" or settings is None or not self._pairing_limiter.allow(remote_address):
            raise LocalAuthError("auth_bootstrap_forbidden", "device pairing is unavailable")
        try:
            peer = ipaddress.ip_address(remote_address)
            token = presented_token if isinstance(presented_token, bytes) else base64.b64decode(
                (presented_token or "").encode("ascii"), validate=True
            )
        except (ValueError, UnicodeEncodeError, binascii.Error):
            self._pairing_limiter.record_failure(remote_address)
            raise LocalAuthError("auth_bootstrap_forbidden", "device pairing is unavailable")
        allowed = any(peer.version == network.version and peer in network for network in settings.allowed_networks)
        if not allowed or not hmac.compare_digest(token, settings.token_bytes):
            self._pairing_limiter.record_failure(remote_address)
            raise LocalAuthError("auth_bootstrap_forbidden", "device pairing is unavailable")
        return "device", settings.token_fingerprint

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
        return self.metadata_store.connect()
