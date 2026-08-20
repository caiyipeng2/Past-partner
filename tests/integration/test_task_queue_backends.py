"""P4-06 queue lifecycle against the configured metadata backends."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from src.server.application import Application
from src.server.config import ServerConfig
from src.services.master_key import MASTER_KEY_BYTES, MASTER_KEY_ENV_VAR


class TaskQueueBackendIntegrationTests(unittest.TestCase):
    def test_sqlite_queue_lifecycle_uses_encrypted_payload(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="past-partner-task-"))
        key = base64.b64encode(b"i" * MASTER_KEY_BYTES).decode("ascii")
        try:
            with patch.dict(os.environ, {MASTER_KEY_ENV_VAR: key}):
                application = Application.from_config(
                    ServerConfig(data_dir=root, web_dir=Path.cwd() / "web", mode="test")
                )
            try:
                owner_id = application.auth.owner_id
                task = application.task_queue.enqueue(
                    owner_id,
                    "integration.echo",
                    {"secret": "integration-payload"},
                    now="2026-08-20T10:00:00+00:00",
                )
                lease = application.task_queue.claim(
                    "integration-worker",
                    now="2026-08-20T10:00:01+00:00",
                    lease_seconds=30,
                )
                completed = application.task_queue.complete(
                    owner_id,
                    task.id,
                    "integration-worker",
                    result={"ok": True},
                    now="2026-08-20T10:00:02+00:00",
                )
                self.assertEqual(task.id, lease.id)
                self.assertEqual("succeeded", completed.state.value)
                self.assertNotIn(b"integration-payload", application.metadata_store.database_path.read_bytes())
            finally:
                application.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_real_postgresql_queue_lifecycle_is_opt_in_and_disposable(self) -> None:
        dsn = os.environ.get("PAST_PARTNER_METADATA_DSN")
        if not dsn or os.environ.get("PAST_PARTNER_METADATA_TEST_DISPOSABLE") != "1":
            self.skipTest("real PostgreSQL queue test requires a disposable DSN")
        try:
            import psycopg
        except ModuleNotFoundError:
            self.skipTest("psycopg is not installed")

        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute("DROP SCHEMA public CASCADE")
            connection.execute("CREATE SCHEMA public")
        root = Path(tempfile.mkdtemp(prefix="past-partner-task-pg-"))
        key = base64.b64encode(b"p" * MASTER_KEY_BYTES).decode("ascii")
        try:
            with patch.dict(os.environ, {MASTER_KEY_ENV_VAR: key}):
                application = Application.from_config(
                    ServerConfig(
                        data_dir=root,
                        web_dir=Path.cwd() / "web",
                        mode="test",
                        metadata_backend="postgresql",
                        metadata_dsn=dsn,
                    )
                )
            try:
                owner_id = application.auth.owner_id
                task = application.task_queue.enqueue(
                    owner_id,
                    "integration.echo",
                    {"value": "postgres"},
                    now="2026-08-20T10:00:00+00:00",
                )
                lease = application.task_queue.claim(
                    "integration-worker",
                    now="2026-08-20T10:00:01+00:00",
                    lease_seconds=30,
                )
                self.assertEqual(task.id, lease.id)
                completed = application.task_queue.complete(
                    owner_id,
                    task.id,
                    "integration-worker",
                    result={"ok": True},
                    now="2026-08-20T10:00:02+00:00",
                )
                self.assertEqual("succeeded", completed.state.value)
            finally:
                application.close()
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
