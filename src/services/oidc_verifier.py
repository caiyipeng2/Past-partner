"""Bounded OIDC ID-token verification for the production account boundary."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import re
import socket
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_TOKEN_BYTES = 16 * 1024
_MAX_KID_LENGTH = 128
_MAX_SUBJECT_LENGTH = 256
_MAX_TENANT_LENGTH = 128
_CLOCK_SKEW_SECONDS = 60
_MAX_JWKS_KEYS = 32
_MAX_JWKS_BYTES = 256 * 1024
_JWKS_FETCH_TIMEOUT_SECONDS = 5.0
_JWKS_REFRESH_INTERVAL_SECONDS = 60.0


class OidcAuthError(ValueError):
    """Stable OIDC configuration or token validation failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class OidcClaims:
    issuer: str
    subject: str
    audience: str
    tenant_id: str
    expires_at: datetime


class OidcVerifier:
    """Verify signed OIDC ID tokens against an administrator-supplied JWKS.

    JWKS may be supplied inline or through an administrator-configured HTTPS URI.
    Remote discovery, nonce validation, and refresh tokens remain separate
    lifecycle work so an unavailable identity provider cannot silently weaken auth.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks: Mapping[str, Any] | None,
        clock: Callable[[], datetime] | None = None,
        jwks_uri: str | None = None,
        jwks_fetcher: Callable[[str], Mapping[str, Any]] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        refresh_interval_seconds: float = _JWKS_REFRESH_INTERVAL_SECONDS,
    ) -> None:
        self.issuer = _bounded_text(issuer, "issuer", 2048)
        self.audience = _bounded_text(audience, "audience", 256)
        if not self.issuer.startswith("https://"):
            raise OidcAuthError("oidc_configuration_invalid", "OIDC issuer must use HTTPS")
        self._jwks_uri = _validate_jwks_uri(jwks_uri)
        if refresh_interval_seconds <= 0:
            raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS refresh interval is invalid")
        self._refresh_interval_seconds = float(refresh_interval_seconds)
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._jwks_fetcher = jwks_fetcher or _fetch_remote_jwks
        self._keys = _load_rsa_keys(jwks, allow_empty=self._jwks_uri is not None)
        self._last_refresh_at = float("-inf")
        self._keys_lock = threading.RLock()
        self._clock = clock or (lambda: datetime.now(UTC))

    def verify(self, token: str) -> OidcClaims:
        if not isinstance(token, str) or not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise OidcAuthError("oidc_token_invalid", "OIDC token is invalid")
        parts = token.split(".")
        if len(parts) != 3:
            raise OidcAuthError("oidc_token_invalid", "OIDC token is invalid")
        encoded_header, encoded_claims, encoded_signature = parts
        try:
            header = _json_object(_decode_segment(encoded_header))
            claims = _json_object(_decode_segment(encoded_claims))
            signature = _decode_segment(encoded_signature)
        except (OidcAuthError, UnicodeDecodeError, json.JSONDecodeError):
            raise OidcAuthError("oidc_token_invalid", "OIDC token is invalid") from None

        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise OidcAuthError("oidc_token_invalid", "OIDC token algorithm is not allowed")
        kid = header["kid"]
        if not kid or len(kid) > _MAX_KID_LENGTH:
            raise OidcAuthError("oidc_token_invalid", "OIDC signing key is not configured")
        key = self._key_for_kid(kid)
        if key is None:
            raise OidcAuthError("oidc_token_invalid", "OIDC signing key is not configured")
        try:
            key.verify(
                signature,
                f"{encoded_header}.{encoded_claims}".encode("ascii"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (InvalidSignature, ValueError):
            raise OidcAuthError("oidc_signature_invalid", "OIDC token signature is invalid") from None

        if claims.get("iss") != self.issuer:
            raise OidcAuthError("oidc_claims_invalid", "OIDC issuer claim is invalid")
        if not _audience_matches(claims.get("aud"), self.audience):
            raise OidcAuthError("oidc_claims_invalid", "OIDC audience claim is invalid")
        subject = _bounded_text(claims.get("sub"), "subject", _MAX_SUBJECT_LENGTH)
        expires_at = _timestamp(claims.get("exp"), "expiration")
        now = self._now()
        if expires_at <= now:
            raise OidcAuthError("oidc_token_expired", "OIDC token has expired")
        issued_at = claims.get("iat")
        if issued_at is not None and _timestamp(issued_at, "issued-at") > now + _skew():
            raise OidcAuthError("oidc_claims_invalid", "OIDC issued-at claim is in the future")
        not_before = claims.get("nbf")
        if not_before is not None and _timestamp(not_before, "not-before") > now + _skew():
            raise OidcAuthError("oidc_token_not_active", "OIDC token is not active")

        tenant_value = claims.get("tid", claims.get("tenant_id", self.issuer))
        tenant_id = _bounded_text(tenant_value, "tenant", _MAX_TENANT_LENGTH)
        return OidcClaims(
            issuer=self.issuer,
            subject=subject,
            audience=self.audience,
            tenant_id=tenant_id,
            expires_at=expires_at,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise OidcAuthError("oidc_configuration_invalid", "OIDC clock must be timezone-aware")
        return value.astimezone(UTC)

    def _key_for_kid(self, kid: str) -> rsa.RSAPublicKey | None:
        with self._keys_lock:
            key = self._keys.get(kid)
            if key is not None or self._jwks_uri is None:
                return key
            now = self._monotonic_clock()
            if now - self._last_refresh_at < self._refresh_interval_seconds:
                return None
            self._last_refresh_at = now
            try:
                refreshed = self._jwks_fetcher(self._jwks_uri)
                self._keys = _load_rsa_keys(refreshed)
            except OidcAuthError as exc:
                if exc.code == "oidc_keys_unavailable":
                    raise OidcAuthError(
                        "oidc_keys_unavailable",
                        "OIDC signing keys are unavailable",
                    ) from exc
                raise
            except Exception as exc:
                raise OidcAuthError("oidc_keys_unavailable", "OIDC signing keys are unavailable") from exc
            return self._keys.get(kid)


def _load_rsa_keys(
    jwks: Mapping[str, Any] | None,
    *,
    allow_empty: bool = False,
) -> dict[str, rsa.RSAPublicKey]:
    if not isinstance(jwks, Mapping):
        raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS is invalid")
    raw_keys = jwks.get("keys")
    if raw_keys is None and allow_empty:
        return {}
    if not isinstance(raw_keys, list):
        raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS is invalid")
    if len(raw_keys) > _MAX_JWKS_KEYS:
        raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS is too large")
    keys: dict[str, rsa.RSAPublicKey] = {}
    for item in raw_keys:
        if not isinstance(item, Mapping):
            raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS is invalid")
        kid = item.get("kid")
        if (
            not isinstance(kid, str)
            or not kid
            or len(kid) > _MAX_KID_LENGTH
            or item.get("kty") != "RSA"
            or item.get("alg") != "RS256"
            or item.get("use") not in {None, "sig"}
        ):
            continue
        try:
            modulus = int.from_bytes(_decode_segment(item["n"]), "big")
            exponent = int.from_bytes(_decode_segment(item["e"]), "big")
            if modulus <= 0 or exponent <= 1:
                raise ValueError
            public_key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
            if public_key.key_size < 2048:
                raise ValueError
            keys[kid] = public_key
        except (KeyError, OidcAuthError, TypeError, ValueError):
            raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS is invalid") from None
    if not keys and not allow_empty:
        raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS has no usable RSA signing key")
    return keys


def _validate_jwks_uri(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 2048:
        raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS URI is invalid")
    uri = value.strip()
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS URI must use HTTPS")
    return uri


def _fetch_remote_jwks(uri: str) -> Mapping[str, Any]:
    request = Request(uri, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=_JWKS_FETCH_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_JWKS_BYTES + 1)
    except (HTTPError, URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise OidcAuthError("oidc_keys_unavailable", "OIDC signing keys are unavailable") from exc
    if len(raw) > _MAX_JWKS_BYTES:
        raise OidcAuthError("oidc_keys_unavailable", "OIDC signing keys are unavailable")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OidcAuthError("oidc_keys_unavailable", "OIDC signing keys are unavailable") from exc
    if not isinstance(payload, Mapping):
        raise OidcAuthError("oidc_keys_unavailable", "OIDC signing keys are unavailable")
    return payload


def _decode_segment(value: object) -> bytes:
    if not isinstance(value, str) or not value or _BASE64URL.fullmatch(value) is None:
        raise OidcAuthError("oidc_token_invalid", "OIDC token encoding is invalid")
    padding_length = (-len(value)) % 4
    try:
        return base64.b64decode(value + "=" * padding_length, altchars=b"-_", validate=True)
    except (ValueError, base64.binascii.Error):
        raise OidcAuthError("oidc_token_invalid", "OIDC token encoding is invalid") from None


def _json_object(raw: bytes) -> Mapping[str, Any]:
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, Mapping):
        raise OidcAuthError("oidc_token_invalid", "OIDC token JSON is invalid")
    return value


def _audience_matches(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    return isinstance(value, list) and any(item == expected for item in value if isinstance(item, str))


def _bounded_text(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise OidcAuthError("oidc_claims_invalid", f"OIDC {label} claim is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > limit or any(ord(character) < 33 for character in normalized):
        raise OidcAuthError("oidc_claims_invalid", f"OIDC {label} claim is invalid")
    return normalized


def _timestamp(value: object, label: str) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OidcAuthError("oidc_claims_invalid", f"OIDC {label} claim is invalid")
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise OidcAuthError("oidc_claims_invalid", f"OIDC {label} claim is invalid") from None


def _skew() -> timedelta:
    return timedelta(seconds=_CLOCK_SKEW_SECONDS)
