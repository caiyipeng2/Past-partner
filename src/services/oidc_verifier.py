"""Bounded OIDC ID-token verification for the production account boundary."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import re
from typing import Any

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

    JWKS is intentionally supplied as configuration in this first slice. Remote
    discovery, key rotation, nonce validation, and refresh tokens remain separate
    lifecycle work so an unavailable identity provider cannot silently weaken auth.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks: Mapping[str, Any],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.issuer = _bounded_text(issuer, "issuer", 2048)
        self.audience = _bounded_text(audience, "audience", 256)
        if not self.issuer.startswith("https://"):
            raise OidcAuthError("oidc_configuration_invalid", "OIDC issuer must use HTTPS")
        self._keys = _load_rsa_keys(jwks)
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
        if not kid or len(kid) > _MAX_KID_LENGTH or kid not in self._keys:
            raise OidcAuthError("oidc_token_invalid", "OIDC signing key is not configured")
        try:
            self._keys[kid].verify(
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


def _load_rsa_keys(jwks: Mapping[str, Any]) -> dict[str, rsa.RSAPublicKey]:
    if not isinstance(jwks, Mapping) or not isinstance(jwks.get("keys"), list):
        raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS is invalid")
    if len(jwks["keys"]) > _MAX_JWKS_KEYS:
        raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS is too large")
    keys: dict[str, rsa.RSAPublicKey] = {}
    for item in jwks["keys"]:
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
    if not keys:
        raise OidcAuthError("oidc_configuration_invalid", "OIDC JWKS has no usable RSA signing key")
    return keys


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
