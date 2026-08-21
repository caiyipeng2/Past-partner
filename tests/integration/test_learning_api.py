import base64
import http.client
import json
import os
import shutil
import threading
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from src.server.application import Application
from src.server.config import ServerConfig
from src.server.http import create_server


class LearningApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_root = Path.cwd() / ".test-runtime" / str(uuid4())
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.data_root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        key = base64.b64encode(b"l" * 32).decode("ascii")
        with patch.dict(os.environ, {"PAST_PARTNER_MASTER_KEY": key}):
            application = Application.from_config(config)
        self.server = create_server(config, application)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        status, _, session = self.request("POST", "/api/v1/auth/session")
        self.assertEqual(201, status)
        self.auth_token = session["access_token"]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.data_root, ignore_errors=True)

    def request(self, method: str, path: str, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        request_headers = dict(headers or {})
        if getattr(self, "auth_token", None) and "Authorization" not in request_headers:
            request_headers["Authorization"] = f"Bearer {self.auth_token}"
        encoded = None
        if isinstance(body, dict):
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        raw = response.read()
        content_type = response.getheader("Content-Type", "")
        connection.close()
        payload = json.loads(raw) if raw and "application/json" in content_type else raw
        return response.status, dict(response.getheaders()), payload

    def create_persona(self) -> dict:
        status, _, persona = self.request(
            "POST", "/api/v1/personas", {"display_name": "小雅", "relationship_type": "friend"}
        )
        self.assertEqual(201, status)
        return persona

    @staticmethod
    def profile_payload() -> dict:
        return {
            "profile_version": 1,
            "message_count": 2,
            "message_length": {"mean": 5.0, "median": 5.0, "min": 4, "max": 6},
            "vocabulary": {
                "token_count": 4,
                "unique_token_count": 3,
                "top_tokens": [{"value": "你好", "count": 2}],
            },
            "punctuation": {
                "message_usage_rate": 0.5,
                "average_per_message": 0.5,
                "counts": {"！": 1},
                "top_punctuation": [{"value": "！", "count": 1}],
            },
            "emoji": {
                "message_count": 1,
                "usage_rate": 0.5,
                "average_per_message": 0.5,
                "counts": {"🙂": 1},
                "top_emojis": [{"value": "🙂", "count": 1}],
            },
            "cadence": {
                "timestamp_count": 0,
                "interval_count": 0,
                "average_interval_seconds": None,
                "median_interval_seconds": None,
                "active_span_seconds": 0.0,
            },
            "emotion_tendency": {
                "counts": {"positive": 1, "negative": 0, "neutral": 1},
                "rates": {"positive": 0.5, "negative": 0.0, "neutral": 0.5},
            },
            "preferred_forms_of_address": [{"value": "小雅", "count": 1}],
            "relationship_context": {"relationship_type": "friend"},
            "relationship_behavior": {
                "source_message_count": 2,
                "persona_message_count": 2,
                "user_message_count": 0,
                "persona_share": 1.0,
            },
        }

    @staticmethod
    def memory_payload(review_state: str = "needs_review") -> dict:
        return {
            "memory_version": 1,
            "source_record_count": 1,
            "accepted_record_count": 1,
            "candidate_count": 1,
            "candidates": [
                {
                    "memory_id": "a" * 64,
                    "kind": "event",
                    "text": "周末一起去看电影",
                    "source_record_ids": ["b" * 64],
                    "occurred_at": "2026-08-21T09:00:00+00:00",
                    "confidence": 0.8,
                    "review_state": review_state,
                    "speaker_scope": "persona",
                }
            ],
            "relationship_context": {"relationship_type": "friend"},
        }

    def test_learning_roundtrip_review_and_retrieval(self) -> None:
        persona = self.create_persona()
        base = f"/api/v1/personas/{persona['id']}/learning"
        profile = self.profile_payload()
        status, _, saved_profile = self.request("PUT", f"{base}/style-profile", {"profile": profile})
        self.assertEqual(200, status)
        self.assertEqual(profile, saved_profile)
        status, _, loaded_profile = self.request("GET", f"{base}/style-profile")
        self.assertEqual(200, status)
        self.assertEqual(profile, loaded_profile)

        memory = self.memory_payload()
        status, _, saved_memory = self.request("PUT", f"{base}/memory", {"memory": memory})
        self.assertEqual(200, status)
        self.assertEqual(memory, saved_memory)
        status, _, result = self.request("POST", f"{base}/retrieve", {"query": "周末电影"})
        self.assertEqual(200, status)
        self.assertEqual([], result["memories"])
        status, _, reviewed = self.request(
            "PATCH",
            f"{base}/memory/{'a' * 64}",
            {"review_state": "accepted"},
        )
        self.assertEqual(200, status)
        self.assertEqual("accepted", reviewed["candidates"][0]["review_state"])
        status, _, result = self.request("POST", f"{base}/retrieve", {"query": "周末电影"})
        self.assertEqual(200, status)
        self.assertEqual(["a" * 64], [item["memory_id"] for item in result["memories"]])

    def test_learning_delete_cascade_and_unknown_persona(self) -> None:
        persona = self.create_persona()
        base = f"/api/v1/personas/{persona['id']}/learning"
        self.request("PUT", f"{base}/style-profile", {"profile": self.profile_payload()})
        self.request("PUT", f"{base}/memory", {"memory": self.memory_payload()})
        status, _, deleted = self.request("DELETE", f"/api/v1/personas/{persona['id']}")
        self.assertEqual(200, status)
        self.assertEqual(
            {"style_profiles": 1, "long_term_memories": 1, "vector_indexes": 1},
            deleted["deleted_learning"],
        )
        status, _, payload = self.request("GET", f"/api/v1/personas/{persona['id']}/learning/memory")
        self.assertEqual(404, status)
        self.assertEqual("not_found", payload["error"]["code"])

        status, _, payload = self.request("GET", "/api/v1/personas/does-not-exist/learning/memory")
        self.assertEqual(404, status)
        self.assertEqual("not_found", payload["error"]["code"])

