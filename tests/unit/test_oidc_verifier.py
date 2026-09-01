from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
import unittest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.services.oidc_verifier import OidcAuthError, OidcClaims, OidcVerifier


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

    def test_verifies_signed_claims_and_normalizes_tenant(self) -> None:
        claims = self.verifier.verify(self._token(aud=["other", self.audience]))

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


if __name__ == "__main__":
    unittest.main()
