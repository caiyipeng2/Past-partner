from __future__ import annotations

import base64
import http.client
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class OidcAuthHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.issuer = "https://issuer.example"
        self.audience = "past-partner"
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.private_key.private_numbers().public_numbers
        jwks = {
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
        self.now = datetime.now(UTC)
        self.environment = patch.dict(
            "os.environ",
            {MASTER_KEY_ENV_VAR: base64.b64encode(b"o" * MASTER_KEY_BYTES).decode("ascii")},
        )
        self.environment.start()
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="test",
            oidc_issuer=self.issuer,
            oidc_audience=self.audience,
            oidc_jwks_json=json.dumps(jwks),
        )
        self.application = Application.from_config(config)
        self.server = create_server(config, self.application)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.application.close()
        shutil.rmtree(self.root, ignore_errors=True)
        self.environment.stop()

    def _token(
        self,
        *,
        subject: str = "user-1",
        tenant_id: str = "tenant-1",
        expires: datetime | None = None,
    ) -> str:
        claims = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": subject,
            "exp": int((expires or (self.now + timedelta(minutes=5))).timestamp()),
            "iat": int((self.now - timedelta(seconds=1)).timestamp()),
            "tid": tenant_id,
        }
        header = {"alg": "RS256", "kid": "key-1", "typ": "JWT"}
        encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        encoded_claims = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        signature = self.private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{encoded_header}.{encoded_claims}.{_b64url(signature)}"

    def _request(self, method: str, path: str, body: dict[str, object] | None = None, token: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if encoded is not None else {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, json.loads(payload)

    def test_oidc_session_provisions_subject_and_keeps_resource_scope(self) -> None:
        status, session = self._request(
            "POST",
            "/api/v1/auth/oidc/session",
            {"id_token": self._token()},
        )

        self.assertEqual(201, status, session)
        self.assertEqual("tenant-1", session["tenant_id"])
        self.assertEqual("user-1", session["subject"])
        self.assertEqual("member", session["role"])
        self.assertNotIn("id_token", json.dumps(session))
        status, personas = self._request("GET", "/api/v1/personas", token=session["access_token"])
        self.assertEqual(200, status)
        self.assertEqual([], personas["personas"])

    def test_oidc_session_rejects_expired_token_without_echoing_it(self) -> None:
        token = self._token(expires=self.now - timedelta(seconds=1))

        status, payload = self._request(
            "POST",
            "/api/v1/auth/oidc/session",
            {"id_token": token},
        )

        self.assertEqual(401, status)
        self.assertEqual("oidc_token_expired", payload["error"]["code"])
        self.assertNotIn(token, json.dumps(payload))

    def test_oidc_session_rejects_tenant_drift_for_existing_subject(self) -> None:
        status, session = self._request(
            "POST",
            "/api/v1/auth/oidc/session",
            {"id_token": self._token()},
        )
        self.assertEqual(201, status)

        status, payload = self._request(
            "POST",
            "/api/v1/auth/oidc/session",
            {"id_token": self._token(tenant_id="tenant-2")},
        )

        self.assertEqual(401, status)
        self.assertEqual("oidc_identity_conflict", payload["error"]["code"])
        self.assertNotIn(session["access_token"], json.dumps(payload))


if __name__ == "__main__":
    unittest.main()
