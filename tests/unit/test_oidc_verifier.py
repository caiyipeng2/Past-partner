from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.services.oidc_verifier import (
    OidcAuthError,
    OidcClaims,
    OidcVerifier,
    _NoRedirectHandler,
    _fetch_remote_jwks,
)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class OidcVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.issuer = "https://issuer.example"
        self.audience = "past-partner"
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.private_key.private_numbers().public_numbers
        self.jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "key-1",
                    "alg": "RS256",
                    "use": "sig",
                    "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
                    "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
                }
            ]
        }
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        self.verifier = OidcVerifier(
            issuer=self.issuer,
            audience=self.audience,
            jwks=self.jwks,
            clock=lambda: self.now,
        )

    @staticmethod
    def _jwks_for_key(private_key: rsa.RSAPrivateKey, kid: str) -> dict[str, object]:
        numbers = private_key.private_numbers().public_numbers
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": kid,
                    "alg": "RS256",
                    "use": "sig",
                    "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
                    "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
                }
            ]
        }

    def _token(self, **overrides: object) -> str:
        claims = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": "user-1",
            "exp": int((self.now + timedelta(minutes=5)).timestamp()),
            "iat": int((self.now - timedelta(seconds=1)).timestamp()),
            "tid": "tenant-1",
        }
        claims.update(overrides)
        header = {"alg": "RS256", "kid": "key-1", "typ": "JWT"}
        encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        signature = self.private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"

    def _token_with_key(self, private_key: rsa.RSAPrivateKey, kid: str) -> str:
        claims = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": "rotated-user",
            "exp": int((self.now + timedelta(minutes=5)).timestamp()),
            "iat": int((self.now - timedelta(seconds=1)).timestamp()),
            "tid": "tenant-1",
        }
        header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
        encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"

    def test_verifies_signed_claims_and_normalizes_tenant(self) -> None:
        claims = self.verifier.verify(self._token(aud=["other", self.audience], azp=self.audience))

        self.assertEqual(
            OidcClaims(
                issuer=self.issuer,
                subject="user-1",
                audience=self.audience,
                tenant_id="tenant-1",
                expires_at=datetime.fromtimestamp(
                    int((self.now + timedelta(minutes=5)).timestamp()), tz=UTC
                ),
            ),
            claims,
        )

    def test_rejects_invalid_signature_and_claim_boundaries(self) -> None:
        cases = (
            {"iss": "https://other.example"},
            {"aud": "other"},
            {"exp": int((self.now - timedelta(seconds=1)).timestamp())},
            {"sub": ""},
            {"tid": "x" * 257},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(OidcAuthError):
                    self.verifier.verify(self._token(**overrides))

        token = self._token()
        header, claims, signature = token.split(".")
        tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        tampered = f"{header}.{claims}.{tampered_signature}"
        with self.assertRaises(OidcAuthError):
            self.verifier.verify(tampered)

    def test_rejects_unapproved_algorithm_or_key(self) -> None:
        for header in (
            {"alg": "none", "kid": "key-1", "typ": "JWT"},
            {"alg": "RS256", "kid": "missing", "typ": "JWT"},
        ):
            claims = {"iss": self.issuer, "aud": self.audience, "sub": "user-1", "exp": int((self.now + timedelta(minutes=5)).timestamp())}
            encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
            encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
            signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
            signature = self.private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
            token = f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"
            with self.subTest(header=header):
                with self.assertRaises(OidcAuthError):
                    self.verifier.verify(token)

    def test_multi_audience_token_requires_matching_authorized_party(self) -> None:
        for azp in (None, "other-client"):
            with self.subTest(azp=azp), self.assertRaises(OidcAuthError):
                self.verifier.verify(self._token(aud=[self.audience, "other-client"], azp=azp))

        claims = self.verifier.verify(
            self._token(aud=[self.audience, "other-client"], azp=self.audience)
        )
        self.assertEqual("user-1", claims.subject)

    def test_refreshes_remote_jwks_for_a_rotated_signing_key(self) -> None:
        rotated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        calls: list[str] = []

        def fetch(uri: str) -> dict[str, object]:
            calls.append(uri)
            return self._jwks_for_key(rotated_key, "key-2")

        verifier = OidcVerifier(
            issuer=self.issuer,
            audience=self.audience,
            jwks=self.jwks,
            jwks_uri="https://issuer.example/.well-known/jwks.json",
            jwks_fetcher=fetch,
            clock=lambda: self.now,
        )

        claims = verifier.verify(self._token_with_key(rotated_key, "key-2"))

        self.assertEqual("rotated-user", claims.subject)
        self.assertEqual(["https://issuer.example/.well-known/jwks.json"], calls)

    def test_remote_jwks_can_be_loaded_without_inline_keys(self) -> None:
        rotated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        verifier = OidcVerifier(
            issuer=self.issuer,
            audience=self.audience,
            jwks={},
            jwks_uri="https://issuer.example/jwks",
            jwks_fetcher=lambda _uri: self._jwks_for_key(rotated_key, "key-2"),
            clock=lambda: self.now,
        )

        self.assertEqual("rotated-user", verifier.verify(self._token_with_key(rotated_key, "key-2")).subject)

    def test_remote_jwks_failure_is_stable_and_refresh_is_throttled(self) -> None:
        calls: list[str] = []

        def fetch(uri: str) -> dict[str, object]:
            calls.append(uri)
            raise OidcAuthError("oidc_keys_unavailable", "transport detail must stay hidden")

        verifier = OidcVerifier(
            issuer=self.issuer,
            audience=self.audience,
            jwks={},
            jwks_uri="https://issuer.example/jwks",
            jwks_fetcher=fetch,
            clock=lambda: self.now,
            monotonic_clock=lambda: 100.0,
        )
        rotated_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = self._token_with_key(rotated_key, "missing")

        with self.assertRaises(OidcAuthError) as first:
            verifier.verify(token)
        self.assertEqual("oidc_keys_unavailable", first.exception.code)
        self.assertNotIn("transport detail", str(first.exception))

        with self.assertRaises(OidcAuthError) as second:
            verifier.verify(token)
        self.assertEqual("oidc_token_invalid", second.exception.code)
        self.assertEqual(["https://issuer.example/jwks"], calls)

    def test_default_remote_jwks_fetch_rejects_oversized_payload(self) -> None:
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit: int) -> bytes:
                return b"x" * (256 * 1024 + 1)

        from unittest.mock import patch

        with patch("src.services.oidc_verifier._REMOTE_JWKS_OPENER.open", return_value=_Response()):
            with self.assertRaises(OidcAuthError) as captured:
                _fetch_remote_jwks("https://issuer.example/jwks")
        self.assertEqual("oidc_keys_unavailable", captured.exception.code)

    def test_remote_jwks_redirect_is_rejected(self) -> None:
        with self.assertRaises(OidcAuthError) as captured:
            _NoRedirectHandler().redirect_request(None, "http://127.0.0.1/private", 302, "redirect", {})
        self.assertEqual("oidc_keys_unavailable", captured.exception.code)


if __name__ == "__main__":
    unittest.main()
