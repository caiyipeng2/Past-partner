import hashlib
import http.client
import json
import shutil
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server


class ImportListRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path("E:/Tools/past_partner_p3_03_import_route_test") / str(uuid4())
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
        status, session = self.request("POST", "/api/v1/auth/session")
        self.assertEqual(201, status)
        self.token = session["access_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.data_root, ignore_errors=True)

    def request(self, method: str, path: str, body: dict | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": f"Bearer {getattr(self, 'token', '')}"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        value = json.loads(response.read())
        connection.close()
        return response.status, value

    def test_lists_only_jobs_for_requested_persona(self) -> None:
        _, first = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "小雅", "relationship_type": "friend"},
        )
        _, second = self.request(
            "POST",
            "/api/v1/personas",
            {"display_name": "妈妈", "relationship_type": "mother"},
        )
        self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": first["id"],
                "source_name": "first.txt",
                "total_bytes": 1,
                "media_type": "text/plain",
            },
        )
        self.request(
            "POST",
            "/api/v1/imports",
            {
                "persona_id": second["id"],
                "source_name": "second.txt",
                "total_bytes": 1,
                "media_type": "text/plain",
            },
        )

        status, value = self.request("GET", f"/api/v1/imports?persona_id={first['id']}")
        self.assertEqual(200, status)
        self.assertEqual(["first.txt"], [item["source_name"] for item in value["imports"]])


if __name__ == "__main__":
    unittest.main()
