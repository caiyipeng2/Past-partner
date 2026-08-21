"""Real PostgreSQL coverage for the shared encrypted metadata store.

The test is opt-in and destructive to the configured database: callers must set
``PAST_PARTNER_METADATA_TEST_DISPOSABLE=1`` alongside a disposable DSN.  This
keeps normal test discovery safe while allowing the same application path to be
verified against a real PostgreSQL server.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from src.server.application import Application
from src.server.config import ServerConfig


class PostgreSQLMetadataIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dsn = os.environ.get("PAST_PARTNER_METADATA_DSN")
        if not dsn:
            raise unittest.SkipTest("PAST_PARTNER_METADATA_DSN is not configured")
        if os.environ.get("PAST_PARTNER_METADATA_TEST_DISPOSABLE") != "1":
            raise unittest.SkipTest("real PostgreSQL test requires an explicitly disposable database")

        import psycopg

        cls._dsn = dsn
        cls._psycopg = psycopg
        cls._reset_schema(psycopg)
        try:
            cls._data_root = Path(tempfile.mkdtemp(prefix="past-partner-pg-integration-"))
            config = ServerConfig(
                host="127.0.0.1",
                port=0,
                data_dir=cls._data_root,
                web_dir=Path.cwd() / "web",
                mode="test",
                metadata_backend="postgresql",
                metadata_dsn=dsn,
                metadata_pool_min_size=1,
                metadata_pool_max_size=3,
            ).validated()
            cls.application = Application.from_config(config)
            session = cls.application.issue_session("127.0.0.1", None)
            cls.owner_id = session["owner_id"]
            cls.access_token = session["access_token"]
        except BaseException:
            application = getattr(cls, "application", None)
            if application is not None:
                application.close()
            data_root = getattr(cls, "_data_root", None)
            if data_root is not None:
                shutil.rmtree(data_root, ignore_errors=True)
            cls._reset_schema(psycopg)
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        application = getattr(cls, "application", None)
        if application is not None:
            application.close()
        data_root = getattr(cls, "_data_root", None)
        if data_root is not None:
            shutil.rmtree(data_root, ignore_errors=True)
        psycopg = getattr(cls, "_psycopg", None)
        if psycopg is not None:
            cls._reset_schema(psycopg)

    @staticmethod
    def _reset_schema(psycopg: object) -> None:
        connect = getattr(psycopg, "connect")
        with connect(os.environ["PAST_PARTNER_METADATA_DSN"], autocommit=True) as connection:
            connection.execute("DROP SCHEMA public CASCADE")
            connection.execute("CREATE SCHEMA public")

    def test_application_round_trip_is_encrypted_owner_scoped_and_cascades(self) -> None:
        principal = self.application.authenticate(f"Bearer {self.access_token}")
        self.assertEqual(self.owner_id, principal.user_id)

        persona = self.application.create_persona(
            self.owner_id,
            {"display_name": "pg-secret-persona", "relationship_type": "friend"},
        )
        self.assertEqual([persona["id"]], [item["id"] for item in self.application.list_personas(self.owner_id)["personas"]])
        self.assertEqual([], self.application.list_personas("other-owner")["personas"])

        content = (
            b"[2026-08-05 21:00] wxid_1: hello\n"
            b"[2026-08-05 21:01] wxid_1: follow-up\n"
        )
        digest = hashlib.sha256(content).hexdigest()
        imported = self.application.create_import(
            self.owner_id,
            {
                "persona_id": persona["id"],
                "source_name": "chat.txt",
                "total_bytes": len(content),
                "media_type": "text/plain",
            },
        )
        import_id = imported["id"]
        self.application.put_chunk(
            self.owner_id,
            import_id,
            0,
            len(content),
            digest,
            BytesIO(content),
        )
        self.application.complete_import(self.owner_id, import_id, {"sha256": digest})
        self.application.set_participant_mapping(
            self.owner_id,
            import_id,
            {"mapping": {"wxid_1": "persona"}},
        )
        preview = self.application.preview_import(self.owner_id, import_id, 10)
        self.assertEqual(2, len(preview["records"]))
        self.assertTrue(all(record["sender_role"] == "persona" for record in preview["records"]))
        self.application.save_import_corrections(
            self.owner_id,
            import_id,
            {
                "corrections": [
                    {"record_id": record["record_id"], "review_state": "accepted", "fields": {}}
                    for record in preview["records"]
                ]
            },
        )

        consent = self.application.create_consent(
            self.owner_id,
            {
                "persona_id": persona["id"],
                "provider_id": "test",
                "model_id": "deterministic",
                "data_category": "persona_text",
                "estimated_cost": 1.0,
                "purpose": "fine_tuning",
                "authorization_scope": f"fine_tuning:{import_id}",
            },
        )
        training_arguments = {
            "persona_id": persona["id"],
            "import_id": import_id,
            "provider_id": "test",
            "model_id": "deterministic",
        }
        estimate = self.application.estimate_training_job(self.owner_id, training_arguments)
        self.assertEqual(2, estimate["sample_count"])
        job = self.application.create_training_job(
            self.owner_id,
            {**training_arguments, "consent_id": consent["id"]},
        )
        refreshed_job = self.application.get_training_job(self.owner_id, job["id"])
        self.assertEqual("completed", refreshed_job["state"])
        self.assertTrue(refreshed_job["artifact_id"])
        self.assertEqual(1, len(self.application.list_training_jobs(self.owner_id)["training_jobs"]))

        conversation = self.application.create_conversation(
            self.owner_id,
            {"persona_id": persona["id"], "provider_id": "test", "model_id": "deterministic"},
        )
        conversation = self.application.send_conversation_message(
            self.owner_id,
            conversation["id"],
            {"content": "hello provider"},
        )
        self.assertEqual(2, len(conversation["messages"]))
        self.assertIn("测试回复", conversation["messages"][-1]["content"])

        with self.application.metadata_store.transaction() as connection:
            encrypted_rows = []
            for table in ("personas", "imports", "consents", "training_jobs", "conversations"):
                encrypted_rows.extend(row[0] for row in connection.execute(f"SELECT encrypted_payload FROM {table}").fetchall())
        encrypted_bytes = b"".join(bytes(value) for value in encrypted_rows)
        self.assertNotIn(b"pg-secret-persona", encrypted_bytes)
        self.assertNotIn(b"hello provider", encrypted_bytes)

        deleted = self.application.delete_persona(self.owner_id, persona["id"])
        self.assertTrue(deleted["deleted"])
        self.assertEqual([], self.application.list_personas(self.owner_id)["personas"])
        self.assertEqual([], self.application.list_imports(self.owner_id)["imports"])
        self.assertEqual([], self.application.list_training_jobs(self.owner_id)["training_jobs"])
        self.assertEqual([], self.application.list_conversations(self.owner_id)["conversations"])
        self.assertEqual([], self.application.list_consents(self.owner_id)["consents"])


if __name__ == "__main__":
    unittest.main()
