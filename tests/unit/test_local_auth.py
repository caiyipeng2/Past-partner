import base64
import hashlib
import ipaddress
import json
import shutil
import sqlite3
import secrets
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.domain.access_scope import AccessScopes
from src.services.local_auth import LocalAuthError, LocalAuthService
from src.services.oidc_verifier import OidcClaims
from src.server.config import DevicePairingSettings
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR, EnvironmentMasterKeyProvider
from src.services.metadata_store import MetadataStoreError


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

    def test_oidc_identity_key_includes_issuer_for_reused_subjects(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        expires_at = datetime.now(UTC) + timedelta(minutes=5)
        first = auth.issue_oidc_session(
            OidcClaims("https://issuer-a.example", "same-subject", "past-partner", "tenant-a", expires_at),
            remote_address="127.0.0.1",
        )
        second = auth.issue_oidc_session(
            OidcClaims("https://issuer-b.example", "same-subject", "past-partner", "tenant-b", expires_at),
            remote_address="127.0.0.1",
        )

        self.assertNotEqual(first["user_id"], second["user_id"])
        self.assertEqual("tenant-a", auth.authenticate(f"Bearer {first['access_token']}").tenant_id)
        self.assertEqual("tenant-b", auth.authenticate(f"Bearer {second['access_token']}").tenant_id)

    def test_oidc_refresh_token_rotates_once_and_is_not_stored_raw(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        expires_at = datetime.now(UTC) + timedelta(minutes=5)
        session = auth.issue_oidc_session(
            OidcClaims("https://issuer.example", "refresh-user", "past-partner", "tenant-a", expires_at),
            remote_address="127.0.0.1",
        )

        self.assertIn("refresh_token", session)
        with closing(sqlite3.connect(self.database_path)) as connection:
            stored = connection.execute("SELECT token_hash FROM oidc_refresh_tokens").fetchone()[0]
        self.assertNotIn(session["refresh_token"].encode("utf-8"), bytes(stored))

        rotated = auth.refresh_oidc_session(session["refresh_token"], remote_address="127.0.0.1")

        self.assertNotEqual(session["access_token"], rotated["access_token"])
        self.assertNotEqual(session["refresh_token"], rotated["refresh_token"])
        self.assertEqual("refresh-user", auth.authenticate(f"Bearer {rotated['access_token']}").subject)
        with self.assertRaises(LocalAuthError) as replay:
            auth.refresh_oidc_session(session["refresh_token"], remote_address="127.0.0.1")
        self.assertEqual("refresh_token_invalid", replay.exception.code)

    def test_oidc_refresh_rejects_when_atomic_rotation_updates_no_row(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        expires_at = datetime.now(UTC) + timedelta(minutes=5)
        session = auth.issue_oidc_session(
            OidcClaims("https://issuer.example", "refresh-race", "past-partner", "tenant-a", expires_at),
            remote_address="127.0.0.1",
        )

        class _ZeroRowUpdate:
            rowcount = 0

        class _Connection:
            def __init__(self) -> None:
                self.raw = sqlite3.connect(self_path)

            @property
            def in_transaction(self) -> bool:
                return self.raw.in_transaction

            def execute(self, query, parameters=()):
                if query.lstrip().startswith("UPDATE oidc_refresh_tokens SET used_at"):
                    return _ZeroRowUpdate()
                return self.raw.execute(query, parameters)

            def rollback(self) -> None:
                self.raw.rollback()

            def commit(self) -> None:
                self.raw.commit()

            def close(self) -> None:
                self.raw.close()

        self_path = self.database_path
        with patch.object(auth.metadata_store, "connect", return_value=_Connection()):
            with self.assertRaises(LocalAuthError) as captured:
                auth.refresh_oidc_session(session["refresh_token"], remote_address="127.0.0.1")

        self.assertEqual("refresh_token_invalid", captured.exception.code)

    def test_oidc_refresh_rejects_a_local_member_identity(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        account = auth.create_local_account("local-member", tenant_id="tenant-a")
        refresh_token = secrets.token_urlsafe(48)
        now = datetime.now(UTC).isoformat()
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                """
                INSERT INTO oidc_refresh_tokens
                    (token_hash, user_id, expires_at, used_at, scopes, created_at)
                VALUES (?, ?, ?, NULL, ?, ?)
                """,
                (
                    hashlib.sha256(refresh_token.encode("utf-8")).digest(),
                    account["user_id"],
                    (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                    AccessScopes.full().serialize(),
                    now,
                ),
            )
            connection.commit()

        with self.assertRaises(LocalAuthError) as captured:
            auth.refresh_oidc_session(refresh_token, remote_address="127.0.0.1")

        self.assertEqual("refresh_token_invalid", captured.exception.code)

    def test_oidc_refresh_rejects_malformed_expiration_timestamp(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        session = auth.issue_oidc_session(
            OidcClaims(
                "https://issuer.example",
                "malformed-expiry",
                "past-partner",
                "tenant-a",
                datetime.now(UTC) + timedelta(minutes=5),
            ),
            remote_address="127.0.0.1",
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute(
                "UPDATE oidc_refresh_tokens SET expires_at = ?",
                ("not-an-iso-timestamp",),
            )
            connection.commit()

        with self.assertRaises(LocalAuthError) as captured:
            auth.refresh_oidc_session(session["refresh_token"], remote_address="127.0.0.1")

        self.assertEqual("refresh_token_invalid", captured.exception.code)

    def test_oidc_refresh_maps_metadata_failure_to_stable_error(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        session = auth.issue_oidc_session(
            OidcClaims(
                "https://issuer.example",
                "metadata-failure",
                "past-partner",
                "tenant-a",
                datetime.now(UTC) + timedelta(minutes=5),
            ),
            remote_address="127.0.0.1",
        )
        with patch.object(
            auth.metadata_store,
            "transaction",
            side_effect=MetadataStoreError("metadata_operational_error", "driver detail"),
        ):
            with self.assertRaises(LocalAuthError) as captured:
                auth.refresh_oidc_session(session["refresh_token"], remote_address="127.0.0.1")

        self.assertEqual("refresh_token_unavailable", captured.exception.code)

    def test_revoke_all_sessions_invalidates_access_and_refresh_tokens(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        oidc_session = auth.issue_oidc_session(
            OidcClaims(
                "https://issuer.example",
                "revoke-user",
                "past-partner",
                "tenant-a",
                datetime.now(UTC) + timedelta(minutes=5),
            ),
            remote_address="127.0.0.1",
        )

        revoked = auth.revoke_all_sessions(oidc_session["user_id"])

        self.assertEqual(1, revoked["revoked_sessions"])
        self.assertEqual(1, revoked["revoked_refresh_tokens"])
        with self.assertRaises(LocalAuthError):
            auth.authenticate(f"Bearer {oidc_session['access_token']}")
        with self.assertRaises(LocalAuthError) as refresh_error:
            auth.refresh_oidc_session(oidc_session["refresh_token"], remote_address="127.0.0.1")
        self.assertEqual("refresh_token_invalid", refresh_error.exception.code)

    def test_revoke_all_sessions_maps_metadata_failure_to_stable_error(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        with patch.object(
            auth.metadata_store,
            "connect",
            side_effect=MetadataStoreError("metadata_operational_error", "driver detail"),
        ):
            with self.assertRaises(LocalAuthError) as captured:
                auth.revoke_all_sessions("missing-or-unavailable")

        self.assertEqual("session_revocation_unavailable", captured.exception.code)

    def test_tenant_member_listing_and_revocation_are_admin_and_tenant_scoped(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        admin = auth.create_local_account("tenant-admin", tenant_id="tenant-a", role="admin")
        member = auth.create_local_account("tenant-member", tenant_id="tenant-a", role="member")
        other = auth.create_local_account("other-member", tenant_id="tenant-b", role="member")
        member_session = auth.issue_account_session(member["user_id"])
        other_session = auth.issue_account_session(other["user_id"])

        listed = auth.list_tenant_members(admin["user_id"])
        self.assertEqual([member["user_id"]], [item["user_id"] for item in listed])
        self.assertEqual("tenant-a", listed[0]["tenant_id"])
        self.assertEqual("member", listed[0]["role"])
        self.assertNotIn("encrypted_payload", listed[0])

        revoked = auth.revoke_tenant_member_sessions(admin["user_id"], member["user_id"])
        self.assertEqual(1, revoked["revoked_sessions"])
        with self.assertRaises(LocalAuthError):
            auth.authenticate(f"Bearer {member_session['access_token']}")
        auth.authenticate(f"Bearer {other_session['access_token']}")
        with self.assertRaises(LocalAuthError) as cross_tenant:
            auth.revoke_tenant_member_sessions(admin["user_id"], other["user_id"])
        self.assertEqual("tenant_member_not_found", cross_tenant.exception.code)

    def test_tenant_member_listing_rejects_non_admin_identity(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        member = auth.create_local_account("tenant-member-only", tenant_id="tenant-a", role="member")

        with self.assertRaises(LocalAuthError) as captured:
            auth.list_tenant_members(member["user_id"])

        self.assertEqual("tenant_admin_required", captured.exception.code)

    def test_tenant_admin_can_change_member_role_without_inconsistent_encrypted_record(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        admin = auth.create_local_account("role-admin", tenant_id="tenant-role", role="admin")
        member = auth.create_local_account("role-member", tenant_id="tenant-role", role="member")
        session = auth.issue_account_session(member["user_id"])

        promoted = auth.update_tenant_member_role(
            admin["user_id"], member["user_id"], "admin"
        )
        self.assertEqual("admin", promoted["role"])
        self.assertEqual("admin", auth.authenticate(f"Bearer {session['access_token']}").role)

        demoted = auth.update_tenant_member_role(
            admin["user_id"], member["user_id"], "member"
        )
        self.assertEqual("member", demoted["role"])
        self.assertEqual("member", auth.authenticate(f"Bearer {session['access_token']}").role)
        with closing(sqlite3.connect(self.database_path)) as connection:
            stored = connection.execute(
                "SELECT encrypted_payload FROM local_users WHERE id = ?",
                (member["user_id"],),
            ).fetchone()[0]
        self.assertNotIn(b'"role":"admin"', bytes(stored))

    def test_tenant_role_update_rejects_self_owner_and_cross_tenant_targets(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        admin = auth.create_local_account("role-admin-boundary", tenant_id="tenant-a", role="admin")
        other = auth.create_local_account("role-other", tenant_id="tenant-b", role="member")

        with self.assertRaises(LocalAuthError) as self_change:
            auth.update_tenant_member_role(admin["user_id"], admin["user_id"], "member")
        self.assertEqual("tenant_role_target_invalid", self_change.exception.code)
        with self.assertRaises(LocalAuthError) as cross_tenant:
            auth.update_tenant_member_role(admin["user_id"], other["user_id"], "admin")
        self.assertEqual("tenant_member_not_found", cross_tenant.exception.code)
        with self.assertRaises(LocalAuthError) as owner:
            auth.update_tenant_member_role(admin["user_id"], auth.owner_id, "member")
        self.assertEqual("tenant_member_not_found", owner.exception.code)

    def test_tenant_role_update_rejects_non_admin_and_invalid_role(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        member = auth.create_local_account("role-member-boundary", tenant_id="tenant-role", role="member")
        target = auth.create_local_account("role-target", tenant_id="tenant-role", role="member")

        with self.assertRaises(LocalAuthError) as non_admin:
            auth.update_tenant_member_role(member["user_id"], target["user_id"], "admin")
        self.assertEqual("tenant_admin_required", non_admin.exception.code)
        with self.assertRaises(LocalAuthError) as non_string:
            auth.update_tenant_member_role(member["user_id"], target["user_id"], [])
        self.assertEqual("tenant_role_invalid", non_string.exception.code)
        with self.assertRaises(LocalAuthError) as invalid_role:
            auth.update_tenant_member_role(member["user_id"], target["user_id"], "owner")
        self.assertEqual("tenant_role_invalid", invalid_role.exception.code)

    def test_tenant_role_update_fails_closed_when_encrypted_identity_drifts(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        admin = auth.create_local_account("role-integrity-admin", tenant_id="tenant-role", role="admin")
        member = auth.create_local_account("role-integrity-member", tenant_id="tenant-role", role="member")
        with closing(sqlite3.connect(self.database_path)) as connection:
            envelope = connection.execute(
                "SELECT encrypted_payload FROM local_users WHERE id = ?",
                (member["user_id"],),
            ).fetchone()[0]
            payload = json.loads(auth.encryption.decrypt(bytes(envelope), auth._aad(member["user_id"])).decode("utf-8"))
            payload["tenant_id"] = "tenant-other"
            corrupted = auth.encryption.encrypt(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8"),
                auth._aad(member["user_id"]),
            )
            connection.execute(
                "UPDATE local_users SET encrypted_payload = ? WHERE id = ?",
                (corrupted, member["user_id"]),
            )
            connection.commit()

        with self.assertRaises(LocalAuthError) as captured:
            auth.update_tenant_member_role(admin["user_id"], member["user_id"], "admin")
        self.assertEqual("tenant_member_invalid", captured.exception.code)

    def test_tenant_role_update_cannot_remove_the_only_administrator(self) -> None:
        auth = LocalAuthService(self.database_path, self.encryption, mode="test")
        admin = auth.create_local_account("only-admin", tenant_id="tenant-only-admin", role="admin")

        with self.assertRaises(LocalAuthError) as captured:
            auth.update_tenant_member_role(admin["user_id"], admin["user_id"], "member")

        self.assertEqual("tenant_role_target_invalid", captured.exception.code)
        self.assertEqual("admin", auth._load_identity(admin["user_id"])["role"])

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
