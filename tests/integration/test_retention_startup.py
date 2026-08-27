import shutil
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from src.server.application import Application
from src.server.config import ServerConfig
from src.services.import_service import ImportState


class RetentionStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.config = ServerConfig(
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="development",
            raw_retention_seconds=0,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.data_root, ignore_errors=True)

    def test_startup_removes_expired_cancelled_import_but_keeps_active_import(self) -> None:
        first = Application.from_config(self.config)
        owner_id = first.auth.owner_id
        persona = first.create_persona(
            owner_id,
            {"display_name": "小雨", "relationship_type": "friend"},
        )
        cancelled = first.imports.create(
            owner_id,
            persona["id"],
            "cancelled.txt",
            1,
            "text/plain",
        )
        first.uploads.cancel(owner_id, cancelled.id)
        expired = replace(
            first.imports.get(owner_id, cancelled.id),
            updated_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
        )
        first.imports.save(owner_id, expired)
        active = first.imports.create(
            owner_id,
            persona["id"],
            "active.txt",
            1,
            "text/plain",
        )

        restarted = Application.from_config(
            replace(self.config, raw_retention_seconds=24 * 60 * 60)
        )

        self.assertIsNone(restarted.imports.repository.get(owner_id, cancelled.id))
        self.assertIsNotNone(restarted.imports.repository.get(owner_id, active.id))

    def test_disabled_startup_does_not_construct_retention_cleanup(self) -> None:
        with patch("src.server.application.RetentionService") as retention_service:
            Application.from_config(self.config)

        retention_service.assert_not_called()

    def test_normalized_startup_removes_only_successfully_normalized_imports(self) -> None:
        first = Application.from_config(self.config)
        owner_id = first.auth.owner_id
        persona = first.create_persona(
            owner_id,
            {"display_name": "标准化人物", "relationship_type": "friend"},
        )
        old = first.imports.create(owner_id, persona["id"], "normalized.txt", 1, "text/plain")
        old_timestamp = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        first.imports.save(
            owner_id,
            replace(
                first.imports.get(owner_id, old.id),
                state=ImportState.UPLOADED,
                normalized_at=old_timestamp,
                updated_at=old_timestamp,
            ),
        )
        uploaded_only = first.imports.create(owner_id, persona["id"], "uploaded.txt", 1, "text/plain")
        first.imports.save(
            owner_id,
            replace(first.imports.get(owner_id, uploaded_only.id), state=ImportState.UPLOADED),
        )

        restarted = Application.from_config(
            replace(self.config, normalized_retention_seconds=24 * 60 * 60)
        )

        self.assertIsNone(restarted.imports.repository.get(owner_id, old.id))
        self.assertIsNotNone(restarted.imports.repository.get(owner_id, uploaded_only.id))


if __name__ == "__main__":
    unittest.main()
