"""Local owner sessions for the loopback development service."""

from __future__ import annotations

import hashlib
import hmac
import json
import binascii
import base64
import ipaddress
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING, Iterable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.services.authenticated_encryption import (
    AuthenticationError,
    AuthenticatedEncryptionService,
    InvalidEncryptedPayloadError,
)
from src.domain.access_scope import AccessScopeError, AccessScopes
from src.services.metadata_store import (
    MetadataConnection,
    MetadataIntegrityError,
    MetadataStore,
    require_metadata_store,
)
from src.services.oidc_verifier import OidcClaims

if TYPE_CHECKING:
    from src.server.config import DevicePairingSettings


class LocalAuthError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class OwnerPrincipal:
    user_id: str
    scopes: AccessScopes = field(default_factory=AccessScopes.full)
    tenant_id: str | None = None
    subject: str | None = None
    role: str = "owner"
    issuer: str | None = None

    def require(self, scope: str) -> None:
        if not self.scopes.allows(scope):
            raise LocalAuthError("insufficient_scope", "required owner scope is unavailable")


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
    """Authenticates owner-compatible local principals and short-lived sessions."""

    _USER_RECORD_VERSION = 1
    _USER_AAD_PREFIX = "past-partner/local-user/v1/"
    _DEFAULT_SESSION_TTL = timedelta(hours=24)
    _IDENTIFIER_MAX_LENGTH = 256
    _ACCOUNT_ROLES = frozenset({"admin", "member"})

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

    def create_local_account(
        self,
        subject: str,
        *,
        tenant_id: str | None = None,
        role: str = "member",
    ) -> dict[str, str]:
        """Create a deterministic local account for development/test identity adapters.

        This is deliberately not a production registration endpoint. Production
        accounts must be created by a later OIDC/OAuth provisioning flow; keeping
        this method mode-gated prevents a local subject string from becoming an
        accidental authentication mechanism when the service is deployed.
        """

        if self.mode not in {"development", "test"}:
            raise LocalAuthError(
                "account_management_unavailable",
                "local account management is unavailable",
            )
        subject = self._identity_text(subject, "subject")
        tenant_id = self._identity_text(tenant_id or secrets.token_hex(16), "tenant_id")
        if role not in self._ACCOUNT_ROLES:
            raise LocalAuthError("account_role_invalid", "local account role is invalid")

        return self._create_account_record("local", subject, tenant_id, role)

    def issue_oidc_session(
        self,
        claims: OidcClaims,
        *,
        remote_address: str,
    ) -> dict[str, str]:
        """Provision or reuse a member account from already verified OIDC claims."""

        if not isinstance(claims, OidcClaims):
            raise LocalAuthError("oidc_claims_invalid", "OIDC claims are invalid")
        issuer = self._identity_text(claims.issuer, "issuer")
        subject = self._identity_text(claims.subject, "subject")
        tenant_id = self._identity_text(claims.tenant_id, "tenant_id")
        user_id = self._find_user_by_identity(issuer, subject)
        if user_id is None:
            try:
                user_id = self._create_account_record(issuer, subject, tenant_id, "member")["user_id"]
            except LocalAuthError as exc:
                if exc.code != "account_subject_exists":
                    raise
                user_id = self._find_user_by_identity(issuer, subject)
                if user_id is None:
                    raise
        else:
            identity = self._load_identity(user_id)
            if identity["tenant_id"] != tenant_id:
                raise LocalAuthError("oidc_identity_conflict", "OIDC subject is bound to another tenant")
        # These scopes authorize the account's own owner_id resources; repository
        # methods never reinterpret owner:write as tenant-wide administration.
        return self._issue_session(user_id, AccessScopes.full(), session_origin="oidc")

    def _create_account_record(
        self,
        issuer: str,
        subject: str,
        tenant_id: str,
        role: str,
    ) -> dict[str, str]:
        user_id = secrets.token_hex(16)
        created_at = datetime.now(UTC).isoformat()
        payload = json.dumps(
            {
                "id": user_id,
                "issuer": issuer,
                "role": role,
                "subject": subject,
                "tenant_id": tenant_id,
                "created_at": created_at,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        envelope = self.encryption.encrypt(payload, self._aad(user_id))
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO local_users (id, kind, record_version, encrypted_payload)
                    VALUES (?, 'member', ?, ?)
                    """,
                    (user_id, self._USER_RECORD_VERSION, envelope),
                )
                connection.execute(
                    """
                    INSERT INTO local_identities (user_id, issuer, tenant_id, subject, role, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, issuer, tenant_id, subject, role, created_at),
                )
                connection.commit()
            except MetadataIntegrityError as exc:
                if connection.in_transaction:
                    connection.rollback()
                raise LocalAuthError(
                    "account_subject_exists",
                    "local account subject already exists",
                ) from exc
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        return {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "subject": subject,
            "role": role,
        }

    def issue_session(
        self,
        remote_address: str,
        presented_bootstrap_token: str | None = None,
        presented_device_bootstrap_token: str | bytes | None = None,
        *,
        scopes: AccessScopes | Iterable[str] | None = None,
    ) -> dict[str, str]:
        try:
            scope_set = AccessScopes.full() if scopes is None else (
                scopes if isinstance(scopes, AccessScopes) else AccessScopes.from_values(scopes)
            )
        except AccessScopeError as exc:
            raise LocalAuthError("scope_invalid", "session scope is invalid") from exc
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

        return self._issue_session(
            self.owner_id,
            scope_set,
            session_origin=session_origin,
            pairing_fingerprint=pairing_fingerprint,
        )

    def issue_account_session(
        self,
        user_id: str,
        remote_address: str = "127.0.0.1",
        *,
        scopes: AccessScopes | Iterable[str] | None = None,
    ) -> dict[str, str]:
        """Issue a loopback-only session for a provisioned local account."""

        if self.mode not in {"development", "test"} or remote_address not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise LocalAuthError("account_session_forbidden", "local account session is unavailable")
        try:
            scope_set = AccessScopes.full() if scopes is None else (
                scopes if isinstance(scopes, AccessScopes) else AccessScopes.from_values(scopes)
            )
        except AccessScopeError as exc:
            raise LocalAuthError("scope_invalid", "session scope is invalid") from exc
        self._load_identity(user_id)
        return self._issue_session(user_id, scope_set)

    def _issue_session(
        self,
        user_id: str,
        scope_set: AccessScopes,
        *,
        session_origin: str = "loopback",
        pairing_fingerprint: bytes | None = None,
    ) -> dict[str, str]:
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
                    (token_hash, user_id, expires_at, session_origin, pairing_token_fingerprint, scopes)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    user_id,
                    expires_at.isoformat(),
                    session_origin,
                    pairing_fingerprint,
                    scope_set.serialize(),
                ),
            )
            connection.commit()
        identity = self._load_identity(user_id)
        return {
            "access_token": token,
            "token_type": "Bearer",
            "owner_id": user_id,
            "user_id": user_id,
            "tenant_id": identity["tenant_id"],
            "subject": identity["subject"],
            "role": identity["role"],
            "expires_at": expires_at.isoformat(),
            "scopes": scope_set.serialize(),
        }

    def authenticate(self, authorization: str | None) -> OwnerPrincipal:
        token = self._parse_bearer(authorization)
        token_hash = self._token_hash(token)
        now = datetime.now(UTC).isoformat()
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT s.user_id, s.expires_at, s.session_origin,
                       s.pairing_token_fingerprint, s.scopes,
                       u.kind, u.record_version, u.encrypted_payload,
                       i.issuer, i.tenant_id, i.subject, i.role
                FROM local_sessions AS s
                JOIN local_users AS u ON u.id = s.user_id
                JOIN local_identities AS i ON i.user_id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        if row is None or str(row[1]) <= now:
            raise LocalAuthError("authentication_required", "a valid owner session is required")
        if row[2] not in {"loopback", "device", "oidc"}:
            raise LocalAuthError("authentication_required", "a valid owner session is required")
        if row[2] == "device":
            current = self.device_pairing
            raw_stored = row[3]
            stored = bytes(raw_stored) if isinstance(raw_stored, (bytes, bytearray, memoryview)) else b""
            if self.mode != "development" or current is None or len(stored) != hashlib.sha256().digest_size:
                raise LocalAuthError("authentication_required", "a valid owner session is required")
            if not hmac.compare_digest(stored, current.token_fingerprint):
                raise LocalAuthError("authentication_required", "a valid owner session is required")
        identity = {
            "user_id": str(row[0]),
            "issuer": str(row[8]),
            "tenant_id": str(row[9]),
            "subject": str(row[10]),
            "role": str(row[11]),
        }
        try:
            if row[5] == "owner":
                self._decode_owner(identity["user_id"], row[6], row[7])
                if identity["role"] != "owner":
                    raise ValueError("owner identity role mismatch")
            elif row[5] == "member":
                self._decode_account(identity, row[6], row[7])
            else:
                raise ValueError("unknown local user kind")
        except (LocalAuthError, ValueError):
            raise LocalAuthError("authentication_required", "a valid owner session is required")
        try:
            scope_set = AccessScopes.parse(str(row[4]))
        except (AccessScopeError, TypeError, ValueError) as exc:
            raise LocalAuthError("authentication_required", "a valid owner session is required") from exc
        return OwnerPrincipal(
            identity["user_id"],
            scope_set,
            identity["tenant_id"],
            identity["subject"],
            identity["role"],
            identity["issuer"],
        )

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
                self._ensure_owner_identity(connection, owner_id)
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
            self._ensure_owner_identity(connection, owner_id)
            connection.commit()
            return owner_id

    def _ensure_owner_identity(self, connection: MetadataConnection, owner_id: str) -> None:
        row = connection.execute(
            "SELECT issuer, tenant_id, subject, role FROM local_identities WHERE user_id = ?",
            (owner_id,),
        ).fetchone()
        expected = ("local", owner_id, "local-owner", "owner")
        if row is None:
            connection.execute(
                """
                INSERT INTO local_identities (user_id, issuer, tenant_id, subject, role, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (owner_id, *expected, datetime.now(UTC).isoformat()),
            )
            return
        if tuple(str(value) for value in row) != expected:
            raise LocalAuthError("auth_owner_record_invalid", "owner identity record is invalid")

    def _load_identity(self, user_id: str) -> dict[str, str]:
        if not isinstance(user_id, str) or not user_id.strip():
            raise LocalAuthError("account_not_found", "local account was not found")
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT u.kind, u.record_version, u.encrypted_payload,
                       i.issuer, i.tenant_id, i.subject, i.role
                FROM local_users AS u
                JOIN local_identities AS i ON i.user_id = u.id
                WHERE u.id = ?
                """,
                (user_id.strip(),),
            ).fetchone()
        if row is None or row[0] not in {"owner", "member"}:
            raise LocalAuthError("account_not_found", "local account was not found")
        identity = {
            "user_id": user_id.strip(),
            "issuer": str(row[3]),
            "tenant_id": str(row[4]),
            "subject": str(row[5]),
            "role": str(row[6]),
        }
        try:
            if row[0] == "owner":
                self._decode_owner(identity["user_id"], row[1], row[2])
                if identity["role"] != "owner":
                    raise ValueError("owner identity role mismatch")
            else:
                self._decode_account(identity, row[1], row[2])
        except (LocalAuthError, ValueError) as exc:
            raise LocalAuthError("account_record_invalid", "local account record is invalid") from exc
        return identity

    def _find_user_by_identity(self, issuer: str, subject: str) -> str | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT user_id FROM local_identities WHERE issuer = ? AND subject = ?",
                (issuer, subject),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def _decode_account(self, identity: dict[str, str], record_version: object, envelope: object) -> None:
        if record_version != self._USER_RECORD_VERSION or not isinstance(envelope, bytes):
            raise ValueError("account record version is unsupported")
        try:
            payload = self.encryption.decrypt(envelope, self._aad(identity["user_id"]))
            value = json.loads(payload.decode("utf-8"))
        except (AuthenticationError, InvalidEncryptedPayloadError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalAuthError("account_record_invalid", "local account record is invalid") from exc
        if (
            not isinstance(value, dict)
            or value.get("id") != identity["user_id"]
            or any(value.get(key) != identity[key] for key in ("issuer", "tenant_id", "subject", "role"))
        ):
            raise ValueError("account identity does not match encrypted record")

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

    @classmethod
    def _identity_text(cls, value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise LocalAuthError("account_identity_invalid", f"local account {field_name} is invalid")
        normalized = value.strip()
        if not normalized or len(normalized) > cls._IDENTIFIER_MAX_LENGTH:
            raise LocalAuthError("account_identity_invalid", f"local account {field_name} is invalid")
        return normalized

    def _connect(self) -> MetadataConnection:
        return self.metadata_store.connect()
