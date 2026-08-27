"""Transport-independent application facade for the public API."""

from __future__ import annotations

import threading
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, BinaryIO, Mapping
from uuid import uuid4

from src.domain.audit_events import AuditAction, AuditEvent, AuditOutcome
from src.domain.personas import PersonaValidationError
from src.services.conversation_repository import ConversationRepository
from src.services.conversation_service import ConversationService
from src.providers.base import ChatMessage, ChatRequest
from src.providers.catalog import ProviderCatalog
from src.providers.configuration import build_provider_adapters
from src.providers.gateway import ProviderGateway
from src.providers.testing import DeterministicTestAdapter, deterministic_test_provider_definition
from src.server.config import ServerConfig
from src.services.authenticated_encryption import AuthenticatedEncryptionService
from src.services.audit_repository import AuditRepository, AuditRepositoryError
from src.services.blob_store import S3BlobStoreSettings, build_blob_store
from src.services.consent_repository import ConsentRepository
from src.services.consent_service import ConsentService
from src.services.deletion_receipt_repository import DeletionReceiptRepository
from src.services.export_service import ExportArtifact, ExportService
from src.services.export_service import ExportServiceError
from src.services.multimodal_consent import MultimodalConsentGate
from src.services.import_repository import ImportRepository
from src.services.import_service import ImportService
from src.services.learning_repository import LearningRepository
from src.services.learning_service import LearningService, LearningServiceError
from src.services.local_auth import LocalAuthService, OwnerPrincipal
from src.services.master_key import MasterKeyProvider, build_master_key_provider
from src.services.metadata_store import MetadataStore, build_metadata_store
from src.services.persona_service import PersonaService
from src.services.persona_repository import PersonaRepository
from src.services.retention_service import RetentionService
from src.services.storage import StorageLayout
from src.services.task_queue import TaskQueue
from src.services.training_dataset import TrainingDatasetBuilder
from src.services.training_repository import TrainingJobRepository
from src.services.training_service import FineTuningService
from src.services.usage_repository import UsageRepository, UsageRepositoryError
from src.services.usage_service import UsageService, UsageServiceError
from src.services.upload_service import UploadError, UploadService


class RequestValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AuditServiceError(RuntimeError):
    """Stable failure when a completed operation cannot be recorded."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class Application:
    def __init__(
        self,
        personas: PersonaService,
        imports: ImportService,
        uploads: UploadService,
        consents: ConsentService,
        master_keys: MasterKeyProvider,
        encryption: AuthenticatedEncryptionService,
        catalog: ProviderCatalog,
        gateway: ProviderGateway,
        auth: LocalAuthService,
        training: FineTuningService,
        conversations: ConversationService,
        learning: LearningService,
        metadata_store: MetadataStore | None = None,
        task_queue: TaskQueue | None = None,
        audit_repository: AuditRepository | None = None,
        usage_repository: UsageRepository | None = None,
        export_service: ExportService | None = None,
        deletion_receipts: DeletionReceiptRepository | None = None,
    ):
        self.personas = personas
        self.imports = imports
        self.uploads = uploads
        self.consents = consents
        self.master_keys = master_keys
        self.encryption = encryption
        self.catalog = catalog
        self.gateway = gateway
        self.auth = auth
        self.training = training
        self.conversations = conversations
        self.learning = learning
        self.metadata_store = metadata_store
        self.task_queue = task_queue
        self.audit_repository = audit_repository
        self.usage_repository = usage_repository
        self.export_service = export_service
        self.deletion_receipts = deletion_receipts
        self.multimodal_consents = MultimodalConsentGate(consents, catalog)
        # Keep create/delete operations that change a persona's child graph atomic in
        # the current single-process runtime. Upload I/O itself remains outside this
        # gate, so active chunks and media inspection can continue independently.
        self._persona_lifecycle_lock = threading.RLock()

    @classmethod
    def from_config(cls, config: ServerConfig) -> "Application":
        config = config.validated()
        storage = StorageLayout(config.data_dir)
        s3_settings = None
        if config.storage_backend == "s3":
            s3_settings = S3BlobStoreSettings(
                endpoint=config.storage_s3_endpoint,
                bucket=config.storage_s3_bucket or "",
                region=config.storage_s3_region,
                access_key=config.storage_s3_access_key,
                secret_key=config.storage_s3_secret_key,
                session_token=config.storage_s3_session_token,
                path_style=config.storage_s3_path_style,
            )
        blob_store = build_blob_store(config.storage_backend, storage, s3_settings=s3_settings)
        metadata_store = build_metadata_store(
            config.metadata_backend,
            storage.database_path(),
            dsn=config.metadata_dsn,
            pool_min_size=config.metadata_pool_min_size,
            pool_max_size=config.metadata_pool_max_size,
        )
        metadata_store.migrate()
        master_keys = build_master_key_provider(
            config.data_dir,
            mode=config.mode,
            master_key_source=config.master_key_source,
            kms_key_id=config.master_key_kms_key_id,
            kms_ciphertext_path=config.master_key_kms_ciphertext_file,
            kms_region=config.master_key_kms_region,
            kms_endpoint=config.master_key_kms_endpoint,
            kms_auto_provision=config.master_key_kms_auto_provision,
        )
        encryption = AuthenticatedEncryptionService(master_keys)
        audit_repository = AuditRepository(metadata_store, encryption)
        usage_repository = UsageRepository(metadata_store, encryption)
        deletion_receipts = DeletionReceiptRepository(metadata_store)
        task_queue = TaskQueue(metadata_store, encryption)
        auth = LocalAuthService(
            metadata_store,
            encryption,
            mode=config.mode,
            bootstrap_token=config.owner_bootstrap_token,
            device_pairing=config.device_pairing_settings,
        )
        persona_repository = PersonaRepository(metadata_store, encryption)
        persona_repository.assign_unowned(auth.owner_id)
        persona_repository.migrate_legacy_json(storage.root / "personas", auth.owner_id)
        personas = PersonaService(persona_repository)
        learning = LearningService(LearningRepository(metadata_store, encryption), personas)
        import_repository = ImportRepository(metadata_store, encryption)
        import_repository.assign_unowned(auth.owner_id)
        import_repository.migrate_legacy_json(
            storage.root / "imports", storage.root / "upload-manifests", auth.owner_id
        )
        imports = ImportService(import_repository, personas, max_import_bytes=config.max_import_bytes)
        consent_repository = ConsentRepository(metadata_store, encryption)
        consents = ConsentService(consent_repository, personas)
        uploads = UploadService(
            storage,
            imports,
            encryption,
            max_chunk_bytes=config.max_chunk_bytes,
            blob_store=blob_store,
        )
        export_service = ExportService(storage, imports, uploads)
        if config.raw_retention_seconds > 0 or config.normalized_retention_seconds > 0:
            RetentionService(
                imports,
                uploads,
                config.raw_retention_seconds,
                config.normalized_retention_seconds,
            ).cleanup(auth.owner_id)
        catalog = ProviderCatalog.default()
        adapters = build_provider_adapters(catalog)
        if config.mode == "test":
            adapters["test"] = DeterministicTestAdapter()
            catalog = ProviderCatalog((*catalog.providers(), deterministic_test_provider_definition()))
        runtime_models = {
            provider_id: adapter.config.allowed_models
            for provider_id, adapter in adapters.items()
            if hasattr(adapter, "config")
        }
        fine_tuning_models = {
            provider_id: adapter.config.fine_tuning_models
            for provider_id, adapter in adapters.items()
            if hasattr(adapter, "config") and hasattr(adapter.config, "fine_tuning_models")
        }
        catalog = catalog.with_configured(set(adapters), runtime_models, fine_tuning_models)
        catalog = catalog.with_pricing_json(config.model_pricing_json)
        gateway = ProviderGateway(catalog, mode=config.mode, adapters=adapters)
        datasets = TrainingDatasetBuilder(storage, uploads)
        training = FineTuningService(
            TrainingJobRepository(metadata_store, encryption),
            datasets,
            consents,
            catalog,
            gateway,
            personas,
        )
        conversations = ConversationService(
            ConversationRepository(metadata_store, encryption),
            personas,
            gateway,
            UsageService(usage_repository, catalog),
        )
        application = cls(
            personas,
            imports,
            uploads,
            consents,
            master_keys,
            encryption,
            catalog,
            gateway,
            auth,
            training,
            conversations,
            learning,
            metadata_store,
            task_queue,
            audit_repository,
            usage_repository,
            export_service,
            deletion_receipts,
        )
        return application

    def close(self) -> None:
        """Release the shared metadata backend exactly once."""

        metadata_store, self.metadata_store = self.metadata_store, None
        if metadata_store is not None:
            metadata_store.close()

    def readiness(self) -> dict[str, Any]:
        """Return a redacted readiness snapshot for the local process.

        Metadata is the only dependency with a stable cross-backend probe in
        this slice.  Backend exceptions are intentionally collapsed to the
        same unavailable state so a health endpoint cannot disclose paths,
        driver messages, or connection details.
        """

        metadata_store = self.metadata_store
        if metadata_store is None:
            metadata_state = "unavailable"
        else:
            connection = None
            try:
                connection = metadata_store.connect()
                connection.execute("SELECT 1")
                metadata_state = "ok"
            except Exception:
                metadata_state = "unavailable"
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        metadata_state = "unavailable"

        ready = metadata_state == "ok"
        return {
            "status": "ready" if ready else "not_ready",
            "service": "past-partner-api",
            "version": "v1",
            "checks": {"metadata_store": metadata_state},
        }

    def issue_session(
        self,
        remote_address: str,
        presented_bootstrap_token: str | None,
        presented_device_bootstrap_token: str | None = None,
    ) -> dict[str, Any]:
        return self.auth.issue_session(
            remote_address,
            presented_bootstrap_token,
            presented_device_bootstrap_token,
        )

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

    def save_style_profile(
        self,
        owner_id: str,
        persona_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            profile = payload["profile"]
        except KeyError as exc:
            raise RequestValidationError("missing_field", "missing profile") from exc
        return self.learning.save_style_profile_payload(owner_id, persona_id, profile).to_dict()

    def get_style_profile(self, owner_id: str, persona_id: str) -> dict[str, Any]:
        return self.learning.get_style_profile(owner_id, persona_id).to_dict()

    def save_learning_memory(
        self,
        owner_id: str,
        persona_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            memory = payload["memory"]
        except KeyError as exc:
            raise RequestValidationError("missing_field", "missing memory") from exc
        return self.learning.save_memory_payload(owner_id, persona_id, memory).to_dict()

    def get_learning_memory(self, owner_id: str, persona_id: str) -> dict[str, Any]:
        return self.learning.get_memory(owner_id, persona_id).to_dict()

    def review_learning_memory(
        self,
        owner_id: str,
        persona_id: str,
        memory_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            review_state = _required_text(payload["review_state"], "review_state")
        except KeyError as exc:
            raise RequestValidationError("missing_field", "missing review_state") from exc
        return self.learning.review_memory(owner_id, persona_id, memory_id, review_state).to_dict()

    def retrieve_learning_memory(
        self,
        owner_id: str,
        persona_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            query = _required_text(payload["query"], "query")
        except KeyError as exc:
            raise RequestValidationError("missing_field", "missing query") from exc
        scopes = payload.get("allowed_speaker_scopes", ("persona", "user"))
        if isinstance(scopes, (str, bytes)) or not isinstance(scopes, (list, tuple)):
            raise RequestValidationError("invalid_allowed_speaker_scopes", "allowed_speaker_scopes must be a list")
        max_candidates = _learning_int(payload.get("max_candidates", 5), "max_candidates")
        max_tokens = _learning_int(payload.get("max_tokens", 800), "max_tokens")
        max_age_days = payload.get("max_age_days")
        if max_age_days is not None:
            max_age_days = _learning_int(max_age_days, "max_age_days")
        result = self.learning.retrieve(
            owner_id,
            persona_id,
            query,
            as_of=payload.get("as_of"),
            max_candidates=max_candidates,
            max_tokens=max_tokens,
            max_age_days=max_age_days,
            allowed_speaker_scopes=tuple(scopes),
        )
        return result.to_dict()

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
        with self._persona_lifecycle_lock:
            self.personas.get(owner_id, persona_id)
            training_cleanup = self.training.delete_for_persona(owner_id, persona_id)
            deleted_imports = self.uploads.delete_persona_imports(owner_id, persona_id)
            deleted_consents = self.consents.delete_for_persona(owner_id, persona_id)
            deleted_conversations = self.conversations.delete_for_persona(owner_id, persona_id)
            deleted_learning = self.learning.delete_for_persona(owner_id, persona_id)
            self.personas.delete(owner_id, persona_id)
            result = {
                "persona_id": persona_id,
                "deleted": True,
                "deleted_imports": deleted_imports,
                "deleted_consents": deleted_consents,
                "deleted_conversations": deleted_conversations,
                "deleted_learning": deleted_learning,
                **training_cleanup,
            }
            self._record_audit(
                owner_id,
                AuditAction.PERSONA_DELETED,
                "persona",
                persona_id,
                metadata={
                    "deleted_children": sum(
                        value
                        for key, value in result.items()
                        if key.startswith("deleted_") and isinstance(value, int)
                    )
                },
            )
            return result

    def export_data(self, owner_id: str) -> dict[str, Any]:
        imports = [
            {
                "job": job.to_dict(),
                "manifest": self.imports.get_manifest(owner_id, job.id) or {},
            }
            for job in self.imports.list(owner_id)
        ]
        learning: list[dict[str, Any]] = []
        for persona in self.personas.list(owner_id):
            entry: dict[str, Any] = {"persona_id": persona.id}
            try:
                entry["style_profile"] = self.learning.get_style_profile(owner_id, persona.id).to_dict()
            except LearningServiceError as exc:
                if exc.code != "learning_not_found":
                    raise
            try:
                entry["memory"] = self.learning.get_memory(owner_id, persona.id).to_dict()
            except LearningServiceError as exc:
                if exc.code != "learning_not_found":
                    raise
            learning.append(entry)
        return {
            "export_version": 1,
            "generated_at": datetime.now(UTC).isoformat(),
            "scope": {
                "raw_payloads_included": False,
                "omitted": ["raw_import_payloads", "provider_side_data", "audit_records"],
            },
            "personas": [persona.to_dict() for persona in self.personas.list(owner_id)],
            "imports": imports,
            "consents": [consent.to_dict() for consent in self.consents.list(owner_id)],
            "training_jobs": [job.to_dict() for job in self.training.list(owner_id)],
            "conversations": [conversation.to_dict() for conversation in self.conversations.list(owner_id)],
            "learning": learning,
        }

    def export_archive(self, owner_id: str) -> ExportArtifact:
        if self.export_service is None:
            raise ExportServiceError("export_unavailable", "owner archive export is unavailable")
        metadata = self.export_data(owner_id)
        # The archive manifest must state the amount of raw material covered by
        # the export.  These values come from the owner-scoped, persisted import
        # records rather than from a second payload read, so the export remains
        # bounded and the declaration cannot trigger another large-file scan.
        raw_imports = metadata.get("imports", [])
        raw_bytes = sum(
            int(item.get("job", {}).get("total_bytes", 0))
            for item in raw_imports
            if isinstance(item, Mapping)
            and isinstance(item.get("job"), Mapping)
            and isinstance(item.get("job", {}).get("total_bytes", 0), int)
        )
        metadata["export_version"] = 2
        metadata["scope"] = {
            "raw_payloads_included": True,
            "omitted": ["provider_side_data", "audit_records"],
        }
        metadata["archive"] = {
            "format": "zip",
            "payload_encoding": "original_plain_bytes",
            "streamed": True,
            "raw_object_count": len(raw_imports) if isinstance(raw_imports, list) else 0,
            "raw_bytes": raw_bytes,
        }
        return self.export_service.create_archive(owner_id, metadata)

    def delete_owner_data(self, owner_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        if payload.get("confirm") != "DELETE":
            raise RequestValidationError(
                "deletion_confirmation_required",
                "explicit deletion confirmation is required",
            )
        if self.metadata_store is None or self.deletion_receipts is None:
            raise AuditServiceError("deletion_unavailable", "data deletion is unavailable")
        with self._persona_lifecycle_lock:
            personas = self.personas.list(owner_id)
            imports = self.imports.list(owner_id)
            if any(job.state.value == "processing" for job in imports):
                raise UploadError("deletion_unavailable", "processing imports must finish before deletion")
            training_jobs = self.training.list(owner_id)
            if any(job.state.value in {"pending", "running"} for job in training_jobs):
                raise UploadError("deletion_unavailable", "processing training jobs must finish before deletion")

            # Remove object-store bytes before the metadata transaction. A failed
            # object deletion aborts the request and never creates a success receipt.
            for job in imports:
                self.uploads.delete_import(owner_id, job.id)

            counts: dict[str, int] = {
                "personas": len(personas),
                "imports": len(imports),
                "training_jobs": len(training_jobs),
                "provider_side_cleanup_limitations": sum(
                    1 for job in training_jobs if job.provider_job_id is not None or job.submission_started
                ),
            }
            with self.metadata_store.transaction(immediate=self.metadata_store.backend_name == "sqlite") as connection:
                for table, key in (
                    ("style_profiles", "style_profiles"),
                    ("long_term_memories", "long_term_memories"),
                    ("vector_indexes", "vector_indexes"),
                    ("conversations", "conversations"),
                    ("consents", "consents"),
                    ("training_jobs", "training_jobs"),
                    ("usage_records", "usage_records"),
                    ("audit_events", "audit_events"),
                    ("task_queue", "task_queue"),
                    ("personas", "personas"),
                    ("imports", "imports"),
                ):
                    counts[key] = connection.execute(
                        f"DELETE FROM {table} WHERE owner_id = ?",
                        (owner_id,),
                    ).rowcount
                # Import and training rows were already enumerated/preflighted;
                # import object cleanup removes their rows before this transaction.
                counts["imports"] = len(imports)
                counts["training_jobs"] = len(training_jobs)
                counts["personas"] = len(personas)
                counts["sessions"] = connection.execute(
                    "DELETE FROM local_sessions WHERE user_id = ?", (owner_id,)
                ).rowcount
                receipt = self.deletion_receipts.create(counts, connection=connection)
            return {
                "deleted": True,
                "receipt_id": receipt["receipt_id"],
                "deleted_at": receipt["deleted_at"],
                "deleted_imports": counts["imports"],
                "deleted_personas": counts["personas"],
                "provider_side_cleanup_limitations": counts["provider_side_cleanup_limitations"],
                "anonymized": True,
            }

    def create_consent(self, owner_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            with self._persona_lifecycle_lock:
                consent = self.consents.create(
                    owner_id=owner_id,
                    persona_id=payload["persona_id"],
                    provider_id=payload["provider_id"],
                    model_id=payload["model_id"],
                    data_category=payload["data_category"],
                    estimated_cost=payload["estimated_cost"],
                    purpose=payload["purpose"],
                    authorization_scope=payload["authorization_scope"],
                )
        except KeyError as exc:
            raise RequestValidationError("missing_field", f"missing {exc.args[0]}") from exc
        return consent.to_dict()

    def list_consents(self, owner_id: str, persona_id: str | None = None) -> dict[str, Any]:
        return {"consents": [consent.to_dict() for consent in self.consents.list(owner_id, persona_id)]}

    def revoke_consent(self, owner_id: str, consent_id: str) -> dict[str, Any]:
        result = self.consents.revoke(owner_id, consent_id).to_dict()
        self._record_audit(
            owner_id,
            AuditAction.CONSENT_REVOKED,
            "consent",
            consent_id,
            metadata={
                "provider_id": result["provider_id"],
                "model_id": result["model_id"],
                "scope": result["authorization_scope"],
            },
        )
        return result

    def authorize_consent(
        self,
        owner_id: str,
        consent_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            decision = self.multimodal_consents.authorize(
                owner_id=owner_id,
                consent_id=consent_id,
                provider_id=payload["provider_id"],
                model_id=payload["model_id"],
                data_category=payload["data_category"],
                authorization_scope=payload["authorization_scope"],
            )
        except KeyError as exc:
            raise RequestValidationError("missing_field", f"missing {exc.args[0]}") from exc
        result = decision.to_dict()
        self._record_audit(
            owner_id,
            AuditAction.CONSENT_AUTHORIZED,
            "consent",
            consent_id,
            metadata={
                "provider_id": result["provider_id"],
                "model_id": result["model_id"],
                "scope": result["authorization_scope"],
            },
        )
        return result

    def create_import(self, owner_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            with self._persona_lifecycle_lock:
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

    def list_imports(self, owner_id: str, persona_id: str | None = None) -> dict[str, Any]:
        if persona_id is None:
            jobs = self.imports.list(owner_id)
        else:
            jobs = self.imports.list_for_persona(owner_id, persona_id)
        return {"imports": [job.to_dict() for job in jobs]}

    def delete_import(self, owner_id: str, import_id: str) -> dict[str, Any]:
        with self._persona_lifecycle_lock:
            result = self.uploads.delete_import(owner_id, import_id)
            self._record_audit(owner_id, AuditAction.IMPORT_DELETED, "import", import_id)
            return result

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

    def inspect_import_media(self, owner_id: str, import_id: str) -> dict[str, Any]:
        return self.uploads.inspect_media(owner_id, import_id)

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

    def estimate_model_cost(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            provider_id = payload["provider_id"]
            model_id = payload["model_id"]
            input_tokens = payload["input_tokens"]
            output_tokens = payload["output_tokens"]
        except KeyError as exc:
            raise RequestValidationError("missing_field", f"missing {exc.args[0]}") from exc
        if not isinstance(provider_id, str) or not isinstance(model_id, str):
            raise RequestValidationError("invalid_model_reference", "provider_id and model_id must be strings")
        estimate = self.catalog.estimate_cost(
            provider_id,
            model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            media_units=payload.get("media_units", 0),
        )
        return estimate.to_dict()

    def estimate_training_job(self, owner_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        arguments = self._training_arguments(payload)
        return self.training.estimate(owner_id, **arguments)

    def create_training_job(self, owner_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        arguments = self._training_arguments(payload, require_consent=True)
        # Deleting a persona must not race an accepted-data handoff. This lock is
        # intentionally held through submission so the dependent job, consent, and
        # import graph either all remain valid or persona deletion runs first.
        with self._persona_lifecycle_lock:
            return self.training.create(owner_id, **arguments).to_dict()

    def list_training_jobs(self, owner_id: str, persona_id: str | None = None) -> dict[str, Any]:
        return {
            "training_jobs": [
                job.to_dict() for job in self.training.list(owner_id, persona_id)
            ]
        }

    def get_training_job(self, owner_id: str, job_id: str) -> dict[str, Any]:
        return self.training.refresh(owner_id, job_id).to_dict()

    def cancel_training_job(self, owner_id: str, job_id: str) -> dict[str, Any]:
        result = self.training.cancel(owner_id, job_id).to_dict()
        if result.get("state") == "cancelled":
            self._record_audit(
                owner_id,
                AuditAction.TRAINING_CANCELLED,
                "training_job",
                job_id,
                metadata={
                    "provider_id": result.get("provider_id"),
                    "model_id": result.get("model_id"),
                },
            )
        return result

    def list_audit_events(
        self,
        owner_id: str,
        *,
        limit: int = 100,
        before: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if self.audit_repository is None:
            raise AuditServiceError("audit_unavailable", "audit records are unavailable")
        try:
            return [event.to_dict() for event in self.audit_repository.list(owner_id, limit=limit, before=before)]
        except AuditRepositoryError as exc:
            raise AuditServiceError("audit_unavailable", "audit records are unavailable") from exc

    def list_usage(
        self,
        owner_id: str,
        *,
        limit: int = 100,
        before: tuple[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        if self.usage_repository is None:
            return []
        try:
            return [record.to_dict() for record in self.usage_repository.list(
                owner_id, limit=limit, before=before
            )]
        except UsageRepositoryError as exc:
            raise AuditServiceError("usage_unavailable", "usage records are unavailable") from exc

    def _record_audit(
        self,
        owner_id: str,
        action: AuditAction,
        resource_type: str,
        resource_id: str,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        if self.audit_repository is None:
            return
        event = AuditEvent(
            id=uuid4().hex,
            owner_id=owner_id,
            action=action,
            outcome=AuditOutcome.SUCCESS,
            resource_type=resource_type,
            resource_id=resource_id,
            occurred_at=datetime.now(UTC).isoformat(),
            metadata=metadata,
        )
        try:
            self.audit_repository.append(event)
        except AuditRepositoryError as exc:
            raise AuditServiceError("audit_unavailable", "audit record could not be persisted") from exc

    def create_conversation(self, owner_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            conversation = self.conversations.create(
                owner_id,
                _required_text(payload["persona_id"], "persona_id"),
                _required_text(payload["provider_id"], "provider_id"),
                _required_text(payload["model_id"], "model_id"),
            )
        except KeyError as exc:
            raise RequestValidationError("missing_field", f"missing {exc.args[0]}") from exc
        return conversation.to_dict()

    def list_conversations(self, owner_id: str, persona_id: str | None = None) -> dict[str, Any]:
        return {
            "conversations": [
                conversation.summary() for conversation in self.conversations.list(owner_id, persona_id)
            ]
        }

    def get_conversation(self, owner_id: str, conversation_id: str) -> dict[str, Any]:
        return self.conversations.get(owner_id, conversation_id).to_dict()

    def send_conversation_message(
        self,
        owner_id: str,
        conversation_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        try:
            content = payload["content"]
        except KeyError as exc:
            raise RequestValidationError("missing_field", "missing content") from exc
        return self.conversations.send(owner_id, conversation_id, content).to_dict()

    def chat(self, payload: Mapping[str, Any], *, owner_id: str | None = None) -> dict[str, Any]:
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
        response = self.gateway.chat(request)
        if owner_id is not None and self.usage_repository is not None:
            try:
                UsageService(self.usage_repository, self.catalog).record_chat(owner_id, request, response)
            except UsageServiceError:
                # The conversation path uses the same best-effort rule: provider
                # success must not become a retry merely because auxiliary usage
                # persistence is unavailable after the provider was charged.
                pass
        return asdict(response)

    @staticmethod
    def _training_arguments(
        payload: Mapping[str, Any],
        *,
        require_consent: bool = False,
    ) -> dict[str, str]:
        fields = ("persona_id", "import_id", "provider_id", "model_id")
        if require_consent:
            fields += ("consent_id",)
        try:
            return {field: _required_text(payload[field], field) for field in fields}
        except KeyError as exc:
            raise RequestValidationError("missing_field", f"missing {exc.args[0]}") from exc


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


def _learning_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestValidationError("invalid_field", f"{field_name} must be an integer")
    return value
