import shutil
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig


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


if __name__ == "__main__":
    unittest.main()
