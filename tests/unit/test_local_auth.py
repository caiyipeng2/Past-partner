import base64
import hashlib
import ipaddress
import shutil
import sqlite3
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.domain.access_scope import AccessScopes
from src.services.local_auth import LocalAuthError, LocalAuthService
from src.server.config import DevicePairingSettings
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR, EnvironmentMasterKeyProvider


class LocalAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        key = base64.b64encode(b"a" * MASTER_KEY_BYTES).decode("ascii")
        self.encryption = AuthenticatedEncryptionService(
            EnvironmentMasterKeyProvider({MASTER_KEY_ENV_VAR: key})
        )
        self.database_path = self.root / "database" / "past-partner.sqlite3"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_bootstraps_owner_and_issues_hashed_bearer_session(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption)

        session = auth.issue_session("127.0.0.1")
        principal = auth.authenticate(f"Bearer {session['access_token']}")

        self.assertEqual(auth.owner_id, principal.user_id)
        self.assertNotIn(session["access_token"].encode("utf-8"), self.database_path.read_bytes())
        with closing(sqlite3.connect(self.database_path)) as connection:
            user_count = connection.execute("SELECT COUNT(*) FROM local_users").fetchone()[0]
            session_count = connection.execute("SELECT COUNT(*) FROM local_sessions").fetchone()[0]
        self.assertEqual(1, user_count)
        self.assertEqual(1, session_count)

    def test_owner_id_survives_restart_and_session_remains_valid(self) -> None:
        first = LocalAuthService(self.database_path, self.encryption)
        session = first.issue_session("127.0.0.1")

        second = LocalAuthService(self.database_path, self.encryption)

        self.assertEqual(first.owner_id, second.owner_id)
        self.assertEqual(first.owner_id, second.authenticate(f"Bearer {session['access_token']}").user_id)

    def test_session_scopes_are_persisted_and_returned_on_principal(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption)
        session = auth.issue_session("127.0.0.1", scopes=["owner:read"])

        principal = auth.authenticate(f"Bearer {session['access_token']}")

        self.assertEqual(AccessScopes.from_values(["owner:read"]), principal.scopes)
        with closing(sqlite3.connect(self.database_path)) as connection:
            stored = connection.execute("SELECT scopes FROM local_sessions").fetchone()[0]
        self.assertEqual("owner:read", stored)

    def test_malformed_persisted_scopes_fail_closed(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption)
        session = auth.issue_session("127.0.0.1")
        with closing(sqlite3.connect(self.database_path)) as connection:
            # Simulate a legacy/tampered row so the authentication parser, rather
            # than only the database CHECK constraint, proves fail-closed behavior.
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE local_sessions SET scopes = 'owner:admin'")
            connection.commit()

        with self.assertRaisesRegex(LocalAuthError, "valid owner session"):
            auth.authenticate(f"Bearer {session['access_token']}")

    def test_missing_malformed_and_expired_sessions_fail_closed(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, session_ttl=timedelta(seconds=1))

        with self.assertRaises(LocalAuthError) as missing:
            auth.authenticate(None)
        self.assertEqual("authentication_required", missing.exception.code)

        with self.assertRaises(LocalAuthError) as malformed:
            auth.authenticate("Basic not-a-bearer")
        self.assertEqual("authentication_required", malformed.exception.code)

        session = auth.issue_session("127.0.0.1")
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE local_sessions SET expires_at = '2000-01-01T00:00:00+00:00'"
            )
            connection.commit()
        with self.assertRaises(LocalAuthError) as expired:
            auth.authenticate(f"Bearer {session['access_token']}")
        self.assertEqual("authentication_required", expired.exception.code)

    def test_non_loopback_bootstrap_is_rejected(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption)

        with self.assertRaises(LocalAuthError) as captured:
            auth.issue_session("192.0.2.10")
        self.assertEqual("auth_bootstrap_forbidden", captured.exception.code)

    def test_production_bootstrap_requires_configured_secret(self) -> None:
        auth = LocalAuthService(
            self.database_path,
            self.encryption,
            mode="production",
            bootstrap_token="bootstrap-secret",
        )

        with self.assertRaises(LocalAuthError) as missing:
            auth.issue_session("127.0.0.1")
        self.assertEqual("auth_bootstrap_required", missing.exception.code)

        session = auth.issue_session("0.0.0.0", "bootstrap-secret")
        self.assertEqual(auth.owner_id, auth.authenticate(f"Bearer {session['access_token']}").user_id)

    def _auth_with_device_pairing(self, token: bytes = b"d" * 32, *, clock=None) -> LocalAuthService:
        settings = DevicePairingSettings(
            host=ipaddress.ip_address("192.168.50.7"),
            allowed_networks=(ipaddress.ip_network("192.168.50.42/32"),),
            token_bytes=token,
            token_fingerprint=hashlib.sha256(token).digest(),
            tls_cert_file=Path("cert.pem"),
            tls_key_file=Path("key.pem"),
        )
        return LocalAuthService(
            self.database_path,
            self.encryption,
            mode="development",
            device_pairing=settings,
            monotonic_clock=clock,
        )

    def test_device_pairing_issues_one_hour_fingerprinted_session(self) -> None:
        token = b"d" * 32
        auth = self._auth_with_device_pairing(token)
        session = auth.issue_session("192.168.50.42", presented_device_bootstrap_token=token)

        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute(
                "SELECT session_origin, pairing_token_fingerprint, expires_at FROM local_sessions WHERE token_hash = ?",
                (sqlite3.Binary(hashlib.sha256(session["access_token"].encode()).digest()),),
            ).fetchone()

        self.assertEqual("device", row[0])
        self.assertEqual(hashlib.sha256(token).digest(), row[1])
        expires_at = datetime.fromisoformat(row[2])
        self.assertLessEqual(expires_at - datetime.now(UTC), timedelta(hours=1, seconds=1))
        self.assertNotIn(token, self.database_path.read_bytes())

    def test_rotating_device_token_invalidates_only_device_session(self) -> None:
        original = b"d" * 32
        auth = self._auth_with_device_pairing(original)
        loopback = auth.issue_session("127.0.0.1")
        device = auth.issue_session("192.168.50.42", presented_device_bootstrap_token=original)

        restarted = self._auth_with_device_pairing(b"e" * 32)
        restarted.authenticate(f"Bearer {loopback['access_token']}")
        with self.assertRaisesRegex(LocalAuthError, "valid owner session"):
            restarted.authenticate(f"Bearer {device['access_token']}")

        production = LocalAuthService(
            self.database_path,
            self.encryption,
            mode="production",
            bootstrap_token="production-secret",
        )
        production.authenticate(f"Bearer {loopback['access_token']}")
        with self.assertRaisesRegex(LocalAuthError, "valid owner session"):
            production.authenticate(f"Bearer {device['access_token']}")

    def test_pairing_failures_are_generic_and_rate_limited(self) -> None:
        now = [100.0]
        auth = self._auth_with_device_pairing(clock=lambda: now[0])

        for _ in range(5):
            with self.assertRaises(LocalAuthError) as captured:
                auth.issue_session("192.168.50.42", presented_device_bootstrap_token=b"x" * 32)
            self.assertEqual("auth_bootstrap_forbidden", captured.exception.code)
        with self.assertRaises(LocalAuthError) as throttled:
            auth.issue_session("192.168.50.42", presented_device_bootstrap_token=b"x" * 32)
        self.assertEqual("auth_bootstrap_forbidden", throttled.exception.code)

    def test_pairing_rejects_non_allowlisted_peer_and_does_not_accept_owner_header(self) -> None:
        auth = self._auth_with_device_pairing()
        for peer, device_token, owner_token in (
            ("192.168.50.43", b"d" * 32, None),
            ("192.168.50.42", None, b"d" * 32),
        ):
            with self.subTest(peer=peer, owner_token=owner_token):
                with self.assertRaises(LocalAuthError) as captured:
                    auth.issue_session(
                        peer,
                        presented_bootstrap_token=owner_token,
                        presented_device_bootstrap_token=device_token,
                    )
            self.assertEqual("auth_bootstrap_forbidden", captured.exception.code)

    def test_malformed_persisted_device_session_fails_closed(self) -> None:
        token = b"d" * 32
        auth = self._auth_with_device_pairing(token)
        session = auth.issue_session("192.168.50.42", presented_device_bootstrap_token=token)
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE local_sessions SET session_origin = 'device', pairing_token_fingerprint = NULL"
            )
            connection.commit()
        with self.assertRaisesRegex(LocalAuthError, "valid owner session"):
            auth.authenticate(f"Bearer {session['access_token']}")

    def test_creates_distinct_local_accounts_and_returns_principal_metadata(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")

        first = auth.create_local_account("oidc:user-a", tenant_id="tenant-a", role="member")
        second = auth.create_local_account("oidc:user-b", tenant_id="tenant-b", role="admin")

        self.assertNotEqual(first["user_id"], second["user_id"])
        self.assertEqual("tenant-a", first["tenant_id"])
        self.assertEqual("admin", second["role"])

        first_session = auth.issue_account_session(first["user_id"])
        second_session = auth.issue_account_session(second["user_id"])
        first_principal = auth.authenticate(f"Bearer {first_session['access_token']}")
        second_principal = auth.authenticate(f"Bearer {second_session['access_token']}")

        self.assertEqual((first["user_id"], "tenant-a", "oidc:user-a", "member"), (
            first_principal.user_id,
            first_principal.tenant_id,
            first_principal.subject,
            first_principal.role,
        ))
        self.assertEqual((second["user_id"], "tenant-b", "oidc:user-b", "admin"), (
            second_principal.user_id,
            second_principal.tenant_id,
            second_principal.subject,
            second_principal.role,
        ))

    def test_duplicate_subject_and_production_account_creation_fail_closed(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        auth.create_local_account("oidc:duplicate")

        with self.assertRaises(LocalAuthError) as duplicate:
            auth.create_local_account("oidc:duplicate")
        self.assertEqual("account_subject_exists", duplicate.exception.code)

        production = LocalAuthService(
            self.database_path,
            self.encryption,
            mode="production",
            bootstrap_token="production-secret",
        )
        with self.assertRaises(LocalAuthError) as disabled:
            production.create_local_account("oidc:blocked")
        self.assertEqual("account_management_unavailable", disabled.exception.code)

    def test_identity_mapping_tampering_fails_closed(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        account = auth.create_local_account("oidc:tampered")
        session = auth.issue_account_session(account["user_id"])
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE local_identities SET role = 'admin', tenant_id = 'other-tenant' WHERE user_id = ?",
                (account["user_id"],),
            )
            connection.commit()

        with self.assertRaisesRegex(LocalAuthError, "valid owner session"):
            auth.authenticate(f"Bearer {session['access_token']}")


if __name__ == "__main__":
    unittest.main()
