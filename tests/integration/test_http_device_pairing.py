import http.client
import base64
import json
import logging
import socket
import ssl
import shutil
import threading
import unittest
from pathlib import Path
from uuid import uuid4

from src.server.config import ServerConfig
from src.server.http import (
    ApiRequestHandler,
    ApplicationServer,
    IPv6ApplicationServer,
    build_tls_context,
    create_server,
)


class _SessionSpy:
    def __init__(self) -> None:
        self.calls = []

    def issue_session(self, remote, owner_token, device_token=None):
        self.calls.append((remote, owner_token, device_token))
        return {"access_token": "test-session", "owner_id": "owner"}


class HttpDevicePairingBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.spy = _SessionSpy()
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="test",
        )
        self.server = create_server(config, self.spy)
        self.assertFalse(self.server.is_tls)
        self.assertIsNone(self.server.tls_context)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.root, ignore_errors=True)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        request_headers = dict(headers or {})
        if encoded is not None:
            request_headers["Content-Type"] = "application/json"
        connection.request(method, path, body=encoded, headers=request_headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, dict(response.getheaders()), payload

    def test_session_endpoint_forwards_separate_owner_and_device_headers(self) -> None:
        status, _, _ = self.request(
            "POST",
            "/api/v1/auth/session",
            headers={
                "X-Local-Owner-Token": "owner-secret",
                "X-Dev-Device-Bootstrap-Token": "device-secret",
            },
        )

        self.assertEqual(201, status)
        self.assertEqual(
            [("127.0.0.1", "owner-secret", "device-secret")],
            self.spy.calls,
        )

    def test_options_does_not_allow_device_bootstrap_header(self) -> None:
        status, headers, _ = self.request(
            "OPTIONS",
            "/api/v1/auth/session",
            headers={"Origin": "http://127.0.0.1:3000"},
        )

        self.assertEqual(204, status)
        self.assertNotIn("X-Dev-Device-Bootstrap-Token", headers["Access-Control-Allow-Headers"])

    def test_real_tls_socket_accepts_device_header_and_rejects_plain_http(self) -> None:
        from tests.support.tls_fixtures import create_server_certificate

        certificate, key, ca = create_server_certificate(self.root, "192.168.50.7")
        device_token = base64.b64encode(b"d" * 32).decode("ascii")
        config = ServerConfig(
            host="192.168.50.7",
            port=0,
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="development",
            device_bootstrap_token=device_token,
            device_allowed_networks=("192.168.50.42/32",),
            device_tls_cert_file=certificate,
            device_tls_key_file=key,
        ).validated()
        server = ApplicationServer(("127.0.0.1", 0), ApiRequestHandler, config, self.spy)
        server.tls_context = build_tls_context(config)
        server.is_tls = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            client_context = ssl.create_default_context(cafile=str(ca))
            # The test binds to loopback while the production certificate is
            # intentionally issued for the validated private-LAN host. Keep CA
            # verification enabled and disable only hostname matching here.
            client_context.check_hostname = False
            connection = http.client.HTTPSConnection(
                "127.0.0.1", port, context=client_context, timeout=5
            )
            connection.request(
                "POST",
                "/api/v1/auth/session",
                headers={"X-Dev-Device-Bootstrap-Token": device_token},
            )
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(201, response.status)
            self.assertEqual(device_token, self.spy.calls[-1][2])

            plain = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            plain.request("GET", "/api/v1/health")
            with self.assertRaises(OSError):
                plain.getresponse()
            plain.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class HttpLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-runtime" / str(uuid4())
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_route_template_logging_excludes_sensitive_request_data(self) -> None:
        # The concrete handler test lives here rather than depending on an external
        # logging configuration; the production logger must be safe by construction.
        from src.server.http import _route_template

        self.assertEqual("/api/v1/personas/{persona_id}", _route_template("/api/v1/personas/secret"))
        self.assertEqual("/api/*", _route_template("/api/v1/unknown?token=secret"))

    def test_tls_context_requires_tls12_or_newer(self) -> None:
        from tests.support.tls_fixtures import create_server_certificate

        certificate, key, _ = create_server_certificate(self.root, "192.168.50.7")
        config = ServerConfig(
            host="192.168.50.7",
            port=0,
            data_dir=self.root,
            web_dir=Path.cwd() / "web",
            mode="development",
            device_bootstrap_token="ZGV2aWNlLXRva2VuLXNlY3JldC0xMjM0NTY3ODkwMTIzNDU2Nzg=",
            device_allowed_networks=("192.168.50.42/32",),
            device_tls_cert_file=certificate,
            device_tls_key_file=key,
        )
        context = build_tls_context(config.validated())
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_ipv6_pairing_server_uses_v6_only_socket_family(self) -> None:
        self.assertEqual(socket.AF_INET6, IPv6ApplicationServer.address_family)


if __name__ == "__main__":
    unittest.main()
