"""Transport-independent application facade for the public API."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, BinaryIO, Mapping

from src.domain.personas import PersonaValidationError
from src.providers.base import ChatMessage, ChatRequest
from src.providers.catalog import ProviderCatalog
from src.providers.configuration import build_openai_compatible_adapters
from src.providers.gateway import ProviderGateway
from src.providers.testing import DeterministicTestAdapter
from src.server.config import ServerConfig
from src.services.database import SQLiteMigrator
from src.services.import_service import ImportService
from src.services.master_key import MasterKeyProvider, build_master_key_provider
from src.services.persona_service import PersonaService
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
        catalog: ProviderCatalog,
        gateway: ProviderGateway,
    ):
        self.personas = personas
        self.imports = imports
        self.uploads = uploads
        self.master_keys = master_keys
        self.catalog = catalog
        self.gateway = gateway

    @classmethod
    def from_config(cls, config: ServerConfig) -> "Application":
        config = config.validated()
        storage = StorageLayout(config.data_dir)
        SQLiteMigrator(storage.database_path()).migrate()
        master_keys = build_master_key_provider(config.data_dir, mode=config.mode)
        personas = PersonaService(storage)
        imports = ImportService(storage, personas, max_import_bytes=config.max_import_bytes)
        uploads = UploadService(storage, imports, max_chunk_bytes=config.max_chunk_bytes)
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
        return cls(personas, imports, uploads, master_keys, catalog, gateway)

    def create_persona(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            persona = self.personas.create(
                display_name=payload["display_name"],
                relationship_type=payload["relationship_type"],
                custom_label=payload.get("custom_label"),
            )
        except KeyError as exc:
            raise RequestValidationError("missing_field", f"missing {exc.args[0]}") from exc
        except PersonaValidationError as exc:
            raise RequestValidationError("invalid_persona", str(exc)) from exc
        return persona.to_dict()

    def list_personas(self) -> dict[str, Any]:
        return {"personas": [persona.to_dict() for persona in self.personas.list()]}

    def create_import(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            job = self.imports.create(
                persona_id=payload["persona_id"],
                source_name=payload["source_name"],
                total_bytes=payload["total_bytes"],
                media_type=payload["media_type"],
            )
        except KeyError as exc:
            raise RequestValidationError("missing_field", f"missing {exc.args[0]}") from exc
        return job.to_dict()

    def get_import(self, import_id: str) -> dict[str, Any]:
        return self.imports.get(import_id).to_dict()

    def put_chunk(
        self,
        import_id: str,
        index: int,
        content_length: int,
        sha256: str,
        stream: BinaryIO,
    ) -> dict[str, Any]:
        return asdict(self.uploads.put_chunk(import_id, index, content_length, sha256, stream))

    def complete_import(self, import_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.uploads.complete(import_id, payload.get("sha256")).to_dict()

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
