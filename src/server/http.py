"""Small development HTTP adapter with strict routing and body limits."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

from src.domain.consents import ConsentValidationError
from src.domain.personas import PersonaValidationError
from src.providers.catalog import CatalogValidationError
from src.providers.gateway import ProviderError
from src.server.application import Application, RequestValidationError
from src.server.config import ServerConfig
from src.services.import_service import ImportNotFoundError, ImportValidationError
from src.services.consent_service import ConsentNotFoundError
from src.services.local_auth import LocalAuthError
from src.services.persona_service import PersonaNotFoundError
from src.services.upload_service import UploadError


logger = logging.getLogger(__name__)
_IMPORT_PATH = re.compile(r"^/api/v1/imports/([A-Za-z0-9._-]+)$")
_MISSING_CHUNKS_PATH = re.compile(r"^/api/v1/imports/([A-Za-z0-9._-]+)/missing-chunks$")
_PROGRESS_PATH = re.compile(r"^/api/v1/imports/([A-Za-z0-9._-]+)/progress$")
_PREVIEW_PATH = re.compile(r"^/api/v1/imports/([A-Za-z0-9._-]+)/preview$")
_PARTICIPANT_MAPPING_PATH = re.compile(
    r"^/api/v1/imports/([A-Za-z0-9._-]+)/participant-mapping$"
)
_CORRECTIONS_PATH = re.compile(r"^/api/v1/imports/([A-Za-z0-9._-]+)/corrections$")
_PERSONA_PATH = re.compile(r"^/api/v1/personas/([A-Za-z0-9._-]+)$")
_CHUNK_PATH = re.compile(r"^/api/v1/imports/([A-Za-z0-9._-]+)/chunks/(\d+)$")
_COMPLETE_PATH = re.compile(r"^/api/v1/imports/([A-Za-z0-9._-]+)/complete$")
_CANCEL_PATH = re.compile(r"^/api/v1/imports/([A-Za-z0-9._-]+)/cancel$")
_CONSENTS_PATH = "/api/v1/consents"
_CONSENT_REVOKE_PATH = re.compile(r"^/api/v1/consents/([A-Za-z0-9._-]+)/revoke$")
_MODEL_COST_ESTIMATE_PATH = "/api/v1/models/cost-estimate"
_STATIC_FILES = {
    "/": "workspace.html",
    "/index.html": "workspace.html",
    "/workspace.js": "workspace.js",
    "/workspace.css": "workspace.css",
}


class ApplicationServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, config: ServerConfig, application: Application):
        super().__init__(address, handler)
        self.config = config
        self.application = application


def create_server(config: ServerConfig, application: Application | None = None) -> ApplicationServer:
    validated = config.validated()
    return ApplicationServer(
        (validated.host, validated.port),
        ApiRequestHandler,
        validated,
        application or Application.from_config(validated),
    )


class ApiRequestHandler(BaseHTTPRequestHandler):
    server: ApplicationServer
    protocol_version = "HTTP/1.1"
    server_version = "PastPartner"
    sys_version = ""

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Chunk-Sha256, Authorization, X-Local-Owner-Token",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        self._dispatch(self._handle_get)

    def do_POST(self) -> None:
        self._dispatch(self._handle_post)

    def do_PUT(self) -> None:
        self._dispatch(self._handle_put)

    def do_PATCH(self) -> None:
        self._dispatch(self._handle_patch)

    def do_DELETE(self) -> None:
        self._dispatch(self._handle_delete)

    def _dispatch(self, operation) -> None:
        try:
            path, _ = self._request_target()
            if self._requires_auth(path):
                self.owner_id = self.server.application.authenticate(
                    self.headers.get("Authorization")
                ).user_id
            else:
                self.owner_id = None
            operation()
        except LocalAuthError as exc:
            status = HTTPStatus.SERVICE_UNAVAILABLE if exc.code.startswith("auth_owner_record_") else HTTPStatus.UNAUTHORIZED
            self._error(status, exc.code, str(exc))
        except RequestValidationError as exc:
            self._error(HTTPStatus.BAD_REQUEST, exc.code, str(exc))
        except (PersonaValidationError, ImportValidationError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, getattr(exc, "code", "validation_error"), str(exc))
        except ConsentValidationError as exc:
            status = {
                "consent_exists": HTTPStatus.CONFLICT,
                "consent_already_revoked": HTTPStatus.CONFLICT,
                "consent_revoked": HTTPStatus.CONFLICT,
                "consent_scope_mismatch": HTTPStatus.CONFLICT,
            }.get(exc.code, HTTPStatus.BAD_REQUEST)
            self._error(status, exc.code, str(exc))
        except ConsentNotFoundError:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "resource not found")
        except CatalogValidationError as exc:
            status = HTTPStatus.UNPROCESSABLE_ENTITY if exc.code in {
                "pricing_unavailable",
                "invalid_usage",
            } else HTTPStatus.BAD_REQUEST
            self._error(status, exc.code, str(exc))
        except (PersonaNotFoundError, ImportNotFoundError):
            self._error(HTTPStatus.NOT_FOUND, "not_found", "resource not found")
        except UploadError as exc:
            # Some upload failures are detected from metadata or the manifest
            # before the request body is consumed. A persistent HTTP/1.1
            # connection cannot be reused safely when unread bytes may remain.
            if self.command == "PUT":
                self.close_connection = True
            status = {
                "import_size_exceeded": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "chunk_too_large": HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "chunk_conflict": HTTPStatus.CONFLICT,
                "upload_incomplete": HTTPStatus.CONFLICT,
                "upload_closed": HTTPStatus.CONFLICT,
                "preview_unavailable": HTTPStatus.CONFLICT,
                "unsupported_format": HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "invalid_record": HTTPStatus.UNPROCESSABLE_ENTITY,
                "empty_source": HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_participant_mapping": HTTPStatus.UNPROCESSABLE_ENTITY,
                "mapping_unavailable": HTTPStatus.CONFLICT,
                "correction_unavailable": HTTPStatus.CONFLICT,
                "invalid_correction": HTTPStatus.UNPROCESSABLE_ENTITY,
                "deletion_unavailable": HTTPStatus.CONFLICT,
                "deletion_failed": HTTPStatus.INTERNAL_SERVER_ERROR,
            }.get(exc.code, HTTPStatus.BAD_REQUEST)
            self._error(status, exc.code, str(exc))
        except ProviderError as exc:
            status = {
                "unknown_provider": HTTPStatus.NOT_FOUND,
                "unknown_model": HTTPStatus.NOT_FOUND,
                "provider_not_configured": HTTPStatus.SERVICE_UNAVAILABLE,
                "provider_unavailable": HTTPStatus.BAD_GATEWAY,
                "provider_http_error": HTTPStatus.BAD_GATEWAY,
                "invalid_provider_response": HTTPStatus.BAD_GATEWAY,
            }.get(exc.code, HTTPStatus.BAD_REQUEST)
            self._error(status, exc.code, str(exc))
        except Exception:
            diagnostic_id = str(uuid4())
            logger.exception("Unhandled request failure diagnostic_id=%s", diagnostic_id)
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "request could not be completed",
                diagnostic_id=diagnostic_id,
            )

    def _handle_get(self) -> None:
        path, query = self._request_target()
        if path == "/api/v1/health":
            self._json(HTTPStatus.OK, {"status": "healthy", "service": "past-partner-api", "version": "v1"})
        elif path == "/api/v1/personas":
            self._json(HTTPStatus.OK, self.server.application.list_personas(self.owner_id))
        elif match := _PERSONA_PATH.fullmatch(path):
            self._json(HTTPStatus.OK, self.server.application.get_persona(self.owner_id, match.group(1)))
        elif path == "/api/v1/providers":
            self._json(HTTPStatus.OK, self.server.application.providers_catalog())
        elif path == "/api/v1/models":
            provider_id = query.get("provider_id", [None])[0]
            self._json(HTTPStatus.OK, self.server.application.models_catalog(provider_id))
        elif path == "/api/v1/data-export":
            self._json(HTTPStatus.OK, self.server.application.export_data(self.owner_id))
        elif path == _CONSENTS_PATH:
            persona_id = query.get("persona_id", [None])[0]
            self._json(HTTPStatus.OK, self.server.application.list_consents(self.owner_id, persona_id))
        elif match := _MISSING_CHUNKS_PATH.fullmatch(path):
            raw_expected = query.get("expected_chunks", [None])[0]
            expected_chunks = None
            if raw_expected is not None:
                try:
                    expected_chunks = int(raw_expected)
                except ValueError as exc:
                    raise RequestValidationError(
                        "invalid_expected_chunk_count",
                        "expected_chunks must be an integer",
                    ) from exc
            self._json(
                HTTPStatus.OK,
                self.server.application.get_missing_chunks(
                    self.owner_id,
                    match.group(1),
                    expected_chunks,
                ),
            )
        elif match := _PROGRESS_PATH.fullmatch(path):
            self._json(
                HTTPStatus.OK,
                self.server.application.get_import_progress(self.owner_id, match.group(1)),
            )
        elif match := _PREVIEW_PATH.fullmatch(path):
            raw_limit = query.get("limit", [None])[0]
            max_records = 20
            if raw_limit is not None:
                try:
                    max_records = int(raw_limit)
                except ValueError as exc:
                    raise RequestValidationError(
                        "invalid_preview_limit",
                        "limit must be an integer",
                    ) from exc
            self._json(
                HTTPStatus.OK,
                self.server.application.preview_import(self.owner_id, match.group(1), max_records),
            )
        elif match := _PARTICIPANT_MAPPING_PATH.fullmatch(path):
            self._json(
                HTTPStatus.OK,
                self.server.application.get_participant_mapping(self.owner_id, match.group(1)),
            )
        elif match := _IMPORT_PATH.fullmatch(path):
            self._json(HTTPStatus.OK, self.server.application.get_import(self.owner_id, match.group(1)))
        elif path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, "route_not_found", "route not found")
        else:
            self._static(path)

    def _handle_post(self) -> None:
        path, _ = self._request_target()
        if path == "/api/v1/auth/session":
            self._json(
                HTTPStatus.CREATED,
                self.server.application.issue_session(
                    self.client_address[0], self.headers.get("X-Local-Owner-Token")
                ),
            )
        elif path == "/api/v1/personas":
            self._json(HTTPStatus.CREATED, self.server.application.create_persona(self.owner_id, self._json_body()))
        elif path == "/api/v1/imports":
            self._json(HTTPStatus.CREATED, self.server.application.create_import(self.owner_id, self._json_body()))
        elif path == "/api/v1/chat":
            self._json(HTTPStatus.OK, self.server.application.chat(self._json_body()))
        elif path == _MODEL_COST_ESTIMATE_PATH:
            self._json(HTTPStatus.OK, self.server.application.estimate_model_cost(self._json_body()))
        elif path == _CONSENTS_PATH:
            self._json(HTTPStatus.CREATED, self.server.application.create_consent(self.owner_id, self._json_body()))
        elif match := _CORRECTIONS_PATH.fullmatch(path):
            self._json(
                HTTPStatus.OK,
                self.server.application.save_import_corrections(
                    self.owner_id,
                    match.group(1),
                    self._json_body(),
                ),
            )
        elif match := _PARTICIPANT_MAPPING_PATH.fullmatch(path):
            self._json(
                HTTPStatus.OK,
                self.server.application.set_participant_mapping(
                    self.owner_id,
                    match.group(1),
                    self._json_body(),
                ),
            )
        elif match := _CANCEL_PATH.fullmatch(path):
            self._json_body()
            self._json(HTTPStatus.OK, self.server.application.cancel_import(self.owner_id, match.group(1)))
        elif match := _CONSENT_REVOKE_PATH.fullmatch(path):
            self._json(HTTPStatus.OK, self.server.application.revoke_consent(self.owner_id, match.group(1)))
        elif match := _COMPLETE_PATH.fullmatch(path):
            self._json(HTTPStatus.OK, self.server.application.complete_import(self.owner_id, match.group(1), self._json_body()))
        else:
            self._error(HTTPStatus.NOT_FOUND, "route_not_found", "route not found")

    def _handle_put(self) -> None:
        path, _ = self._request_target()
        match = _CHUNK_PATH.fullmatch(path)
        if match is None:
            self._error(HTTPStatus.NOT_FOUND, "route_not_found", "route not found")
            return
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self.close_connection = True
            self._error(HTTPStatus.LENGTH_REQUIRED, "content_length_required", "Content-Length is required")
            return
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise RequestValidationError("invalid_content_length", "Content-Length must be an integer") from exc
        if content_length > self.server.config.max_chunk_bytes:
            self.close_connection = True
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "chunk_too_large", "chunk exceeds the configured limit")
            return
        digest = self.headers.get("X-Chunk-Sha256")
        if digest is None:
            self.close_connection = True
            raise RequestValidationError("digest_required", "X-Chunk-Sha256 is required")
        result = self.server.application.put_chunk(
            self.owner_id,
            match.group(1),
            int(match.group(2)),
            content_length,
            digest,
            self.rfile,
        )
        self._json(HTTPStatus.OK, result)

    def _handle_patch(self) -> None:
        path, _ = self._request_target()
        match = _PERSONA_PATH.fullmatch(path)
        if match is None:
            self._error(HTTPStatus.NOT_FOUND, "route_not_found", "route not found")
            return
        self._json(
            HTTPStatus.OK,
            self.server.application.update_persona(
                self.owner_id,
                match.group(1),
                self._json_body(),
            ),
        )

    def _handle_delete(self) -> None:
        path, _ = self._request_target()
        if match := _PERSONA_PATH.fullmatch(path):
            self._json(
                HTTPStatus.OK,
                self.server.application.delete_persona(self.owner_id, match.group(1)),
            )
            return
        match = _IMPORT_PATH.fullmatch(path)
        if match is None:
            self._error(HTTPStatus.NOT_FOUND, "route_not_found", "route not found")
            return
        self._json(
            HTTPStatus.OK,
            self.server.application.delete_import(self.owner_id, match.group(1)),
        )

    def _json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise RequestValidationError("content_length_required", "Content-Length is required")
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise RequestValidationError("invalid_content_length", "Content-Length must be an integer") from exc
        if content_length < 0 or content_length > self.server.config.max_json_bytes:
            self.close_connection = True
            raise RequestValidationError("json_body_too_large", "JSON body exceeds the configured limit")
        try:
            value = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestValidationError("invalid_json", "request body must be valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise RequestValidationError("invalid_json", "request body must be a JSON object")
        return value

    def _static(self, path: str) -> None:
        filename = _STATIC_FILES.get(path)
        if filename is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "resource not found")
            return
        source = (self.server.config.web_dir / filename).resolve()
        if source.parent != self.server.config.web_dir or not source.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not_found", "resource not found")
            return
        content = source.read_bytes()
        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _request_target(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlsplit(self.path)
        decoded = unquote(parsed.path)
        # Reject traversal before route matching even though static serving uses
        # a whitelist; this keeps future asset additions inside the same guard.
        if any(segment == ".." for segment in decoded.replace("\\", "/").split("/")):
            return "/__rejected__", {}
        return decoded, parse_qs(parsed.query)

    @staticmethod
    def _requires_auth(path: str) -> bool:
        return path.startswith("/api/v1/") and path not in {
            "/api/v1/health",
            "/api/v1/auth/session",
        }

    def _json(self, status: HTTPStatus, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _error(
        self,
        status: HTTPStatus,
        code: str,
        message: str,
        *,
        diagnostic_id: str | None = None,
    ) -> None:
        diagnostic_id = diagnostic_id or str(uuid4())
        logger.info("Request error code=%s diagnostic_id=%s", code, diagnostic_id)
        self._json(
            status,
            {
                "error": {
                    "code": code,
                    "message": message,
                    "diagnostic_id": diagnostic_id,
                }
            },
        )

    def end_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and origin in self.server.config.cors_origins:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        logger.info("%s - %s", self.address_string(), format % args)
