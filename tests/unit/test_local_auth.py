import base64
import shutil
import sqlite3
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.local_auth import LocalAuthError, LocalAuthService
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


if __name__ == "__main__":
    unittest.main()
