"""Transport-independent application facade for the public API."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, BinaryIO, Mapping

from src.domain.personas import PersonaValidationError
from src.providers.base import ChatMessage, ChatRequest
from src.providers.catalog import ProviderCatalog
from src.providers.configuration import build_openai_compatible_adapters
from src.providers.gateway import ProviderGateway
from src.providers.testing import DeterministicTestAdapter
from src.server.config import ServerConfig
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.database import SQLiteMigrator
from src.services.import_repository import ImportRepository
from src.services.import_service import ImportService
from src.services.local_auth import LocalAuthService, OwnerPrincipal
from src.services.master_key import MasterKeyProvider, build_master_key_provider
from src.services.persona_service import PersonaService
from src.services.persona_repository import PersonaRepository
from src.services.retention_service import RetentionService
from src.services.storage import StorageLayout
from src.services.upload_service import UploadService


class RequestValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Application:
    def __init__(
        self,
        personas: PersonaService,
        imports: ImportService,
        uploads: UploadService,
        master_keys: MasterKeyProvider,
        encryption: AuthenticatedEncryptionService,
        catalog: ProviderCatalog,
        gateway: ProviderGateway,
        auth: LocalAuthService,
    ):
        self.personas = personas
        self.imports = imports
        self.uploads = uploads
        self.master_keys = master_keys
        self.encryption = encryption
        self.catalog = catalog
        self.gateway = gateway
        self.auth = auth

    @classmethod
    def from_config(cls, config: ServerConfig) -> "Application":
        config = config.validated()
        storage = StorageLayout(config.data_dir)
        SQLiteMigrator(storage.database_path()).migrate()
        master_keys = build_master_key_provider(config.data_dir, mode=config.mode)
        encryption = AuthenticatedEncryptionService(master_keys)
        auth = LocalAuthService(
            storage.database_path(),
            encryption,
            mode=config.mode,
            bootstrap_token=config.owner_bootstrap_token,
        )
        persona_repository = PersonaRepository(storage.database_path(), encryption)
        persona_repository.assign_unowned(auth.owner_id)
        persona_repository.migrate_legacy_json(storage.root / "personas", auth.owner_id)
        personas = PersonaService(persona_repository)
        import_repository = ImportRepository(storage.database_path(), encryption)
        import_repository.assign_unowned(auth.owner_id)
        import_repository.migrate_legacy_json(
            storage.root / "imports", storage.root / "upload-manifests", auth.owner_id
        )
        imports = ImportService(import_repository, personas, max_import_bytes=config.max_import_bytes)
        uploads = UploadService(
            storage, imports, encryption, max_chunk_bytes=config.max_chunk_bytes
        )
        if config.raw_retention_seconds > 0:
            RetentionService(imports, uploads, config.raw_retention_seconds).cleanup(auth.owner_id)
        catalog = ProviderCatalog.default()
        adapters = build_openai_compatible_adapters(catalog)
        if config.mode == "test":
            adapters["test"] = DeterministicTestAdapter()
        runtime_models = {
            provider_id: adapter.config.allowed_models
            for provider_id, adapter in adapters.items()
            if hasattr(adapter, "config")
        }
        catalog = catalog.with_configured(set(adapters) - {"test"}, runtime_models)
        gateway = ProviderGateway(catalog, mode=config.mode, adapters=adapters)
        return cls(personas, imports, uploads, master_keys, encryption, catalog, gateway, auth)

    def issue_session(self, remote_address: str, presented_bootstrap_token: str | None) -> dict[str, Any]:
        return self.auth.issue_session(remote_address, presented_bootstrap_token)

    def authenticate(self, authorization: str | None) -> OwnerPrincipal:
        return self.auth.authenticate(authorization)

    def create_persona(self, owner_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            persona = self.personas.create(
                owner_id=owner_id,
                display_name=payload["display_name"],
                relationship_type=payload["relationship_type"],
                custom_label=payload.get("custom_label"),
                relationship_label=payload.get("relationship_label"),
                preferred_address=payload.get("preferred_address"),
                user_address=payload.get("user_address"),
                relationship_description=payload.get("relationship_description"),
                tone_boundaries=payload.get("tone_boundaries"),
                forbidden_topics=payload.get("forbidden_topics"),
            )
        except KeyError as exc:
            raise RequestValidationError("missing_field", f"missing {exc.args[0]}") from exc
        except PersonaValidationError as exc:
            raise RequestValidationError("invalid_persona", str(exc)) from exc
        return persona.to_dict()

    def list_personas(self, owner_id: str) -> dict[str, Any]:
        return {"personas": [persona.to_dict() for persona in self.personas.list(owner_id)]}

    def get_persona(self, owner_id: str, persona_id: str) -> dict[str, Any]:
        return self.personas.get(owner_id, persona_id).to_dict()

    def update_persona(
        self,
        owner_id: str,
        persona_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            persona = self.personas.update(owner_id, persona_id, payload)
        except PersonaValidationError as exc:
            raise RequestValidationError("invalid_persona", str(exc)) from exc
        return persona.to_dict()

    def delete_persona(self, owner_id: str, persona_id: str) -> dict[str, Any]:
        self.personas.get(owner_id, persona_id)
        deleted_imports = self.uploads.delete_persona_imports(owner_id, persona_id)
        self.personas.delete(owner_id, persona_id)
        return {
            "persona_id": persona_id,
            "deleted": True,
            "deleted_imports": deleted_imports,
        }

    def export_data(self, owner_id: str) -> dict[str, Any]:
        imports = [
            {
                "job": job.to_dict(),
                "manifest": self.imports.get_manifest(owner_id, job.id) or {},
            }
            for job in self.imports.list(owner_id)
        ]
        return {
            "export_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": {
                "raw_payloads_included": False,
                "omitted": ["raw_import_payloads", "provider_side_data", "audit_records"],
            },
            "personas": [persona.to_dict() for persona in self.personas.list(owner_id)],
            "imports": imports,
        }

    def create_import(self, owner_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            arguments: dict[str, Any] = {
                "owner_id": owner_id,
                "persona_id": payload["persona_id"],
            }
            if "files" in payload:
                arguments["files"] = payload["files"]
                arguments["source_name"] = payload.get("source_name")
                arguments["total_bytes"] = payload.get("total_bytes")
                arguments["media_type"] = payload.get("media_type")
            else:
                arguments.update(
                    source_name=payload["source_name"],
                    total_bytes=payload["total_bytes"],
                    media_type=payload["media_type"],
                )
            job = self.imports.create(**arguments)
        except KeyError as exc:
            raise RequestValidationError("missing_field", f"missing {exc.args[0]}") from exc
        return job.to_dict()

    def get_import(self, owner_id: str, import_id: str) -> dict[str, Any]:
        return self.imports.get(owner_id, import_id).to_dict()

    def delete_import(self, owner_id: str, import_id: str) -> dict[str, Any]:
        return self.uploads.delete_import(owner_id, import_id)

    def get_missing_chunks(
        self,
        owner_id: str,
        import_id: str,
        expected_chunks: int | None = None,
    ) -> dict[str, Any]:
        return self.uploads.missing_chunks(owner_id, import_id, expected_chunks)

    def get_import_progress(self, owner_id: str, import_id: str) -> dict[str, Any]:
        return self.uploads.progress(owner_id, import_id)

    def preview_import(
        self,
        owner_id: str,
        import_id: str,
        max_records: int = 20,
    ) -> dict[str, Any]:
        return self.uploads.preview(owner_id, import_id, max_records)

    def set_participant_mapping(
        self,
        owner_id: str,
        import_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            mapping = payload["mapping"]
        except KeyError as exc:
            raise RequestValidationError("missing_field", "missing mapping") from exc
        if not isinstance(mapping, Mapping):
            raise RequestValidationError("invalid_participant_mapping", "mapping must be an object")
        return self.uploads.set_participant_mapping(owner_id, import_id, mapping)

    def get_participant_mapping(self, owner_id: str, import_id: str) -> dict[str, Any]:
        return self.uploads.participant_mapping(owner_id, import_id)

    def save_import_corrections(
        self,
        owner_id: str,
        import_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            corrections = payload["corrections"]
        except KeyError as exc:
            raise RequestValidationError("missing_field", "missing corrections") from exc
        if not isinstance(corrections, list):
            raise RequestValidationError("invalid_correction", "corrections must be a list")
        return self.uploads.save_corrections(owner_id, import_id, corrections)

    def put_chunk(
        self,
        owner_id: str,
        import_id: str,
        index: int,
        content_length: int,
        sha256: str,
        stream: BinaryIO,
    ) -> dict[str, Any]:
        return asdict(self.uploads.put_chunk(owner_id, import_id, index, content_length, sha256, stream))

    def complete_import(self, owner_id: str, import_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.uploads.complete(owner_id, import_id, payload.get("sha256")).to_dict()

    def cancel_import(self, owner_id: str, import_id: str) -> dict[str, Any]:
        return self.uploads.cancel(owner_id, import_id).to_dict()

    def providers_catalog(self) -> dict[str, Any]:
        return {"providers": self.catalog.to_dict()}

    def models_catalog(self, provider_id: str | None) -> dict[str, Any]:
        if provider_id:
            provider = self.catalog.find_provider(provider_id)
            if provider is None:
                raise RequestValidationError("unknown_provider", "provider does not exist")
            return {"provider_id": provider_id, "models": [item.to_dict() for item in provider.models]}
        return {
            "models": [
                {"provider_id": provider.id, **model.to_dict()}
                for provider in self.catalog.providers()
                for model in provider.models
            ]
        }

    def chat(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            raw_messages = payload["messages"]
            if not isinstance(raw_messages, list) or not raw_messages:
                raise RequestValidationError("invalid_messages", "messages must be a non-empty list")
            messages = tuple(_chat_message(item) for item in raw_messages)
            request = ChatRequest(
                provider_id=_required_text(payload["provider_id"], "provider_id"),
                model_id=_required_text(payload["model_id"], "model_id"),
                messages=messages,
                temperature=payload.get("temperature"),
            )
        except KeyError as exc:
            raise RequestValidationError("missing_field", f"missing {exc.args[0]}") from exc
        return asdict(self.gateway.chat(request))


def _chat_message(value: object) -> ChatMessage:
    if not isinstance(value, Mapping):
        raise RequestValidationError("invalid_messages", "each message must be an object")
    try:
        role = _required_text(value["role"], "role")
        content = _required_text(value["content"], "content")
    except KeyError as exc:
        raise RequestValidationError("invalid_messages", f"message missing {exc.args[0]}") from exc
    if role not in {"system", "user", "assistant"}:
        raise RequestValidationError("invalid_messages", "unsupported message role")
    return ChatMessage(role, content)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequestValidationError("invalid_field", f"{field_name} must be a non-empty string")
    return value.strip()
