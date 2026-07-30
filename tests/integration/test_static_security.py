import http.client
import shutil
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server


class StaticSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="development",
        )
        self.server = create_server(config, Application.from_config(config))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.data_root, ignore_errors=True)

    def get(self, path: str):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        status = response.status
        content_type = response.getheader("Content-Type")
        connection.close()
        return status, content_type, body

    def test_serves_allowlisted_web_assets(self) -> None:
        status, content_type, body = self.get("/")
        self.assertEqual(200, status)
        self.assertIn("text/html", content_type)
        self.assertIn(b"<!DOCTYPE html>", body)

    def test_blocks_plain_and_encoded_traversal(self) -> None:
        for path in ("/../package.json", "/%2e%2e/package.json", "/..%2fpackage.json"):
            with self.subTest(path=path):
                status, _, body = self.get(path)
                self.assertEqual(404, status)
                self.assertNotIn(b"personalized-style-companion-ai", body)

    def test_legacy_assets_are_not_served(self) -> None:
        for path in ("/app.js", "/styles.css"):
            with self.subTest(path=path):
                status, _, _ = self.get(path)
                self.assertEqual(404, status)


if __name__ == "__main__":
    unittest.main()
