# Personalized Companion Platform Design

**Status:** Approved design baseline
**Date:** 2026-07-30
**Project root:** `D:\AI开发`

## 中文审阅摘要

- 正式客户端采用 Flutter，同时覆盖 Android 和 iOS；现有 Web 端保留为 PC 调试、数据校对和管理入口。
- 用户必须先创建人物并选择父亲、母亲、其他亲人、朋友、情侣或自定义关系，之后才能导入数据。
- 单次导入任务默认支持最多 3 GiB，通过分片、校验、断点续传、加密暂存和流式解析处理。
- 首批导入微信、QQ、通用聊天文件、数据库、压缩包、文档、图片和音频，并通过解析器插件继续扩展。
- 图片采用完整多模态学习；原始媒体发送给第三方模型前，必须展示厂商、范围和预计费用并获得逐次授权。
- 学习层由风格画像、长期记忆、向量检索和可选微调组成，人物能力不绑定单一模型。
- 模型网关原生覆盖 DeepSeek、小米 MiMo、通义千问、OpenAI、Claude、Gemini 和 Ollama，并支持自定义 API、本地运行时、GGUF、HuggingFace 与后续插件 SDK。
- 用户可按价格、上下文、视觉、音频、Embedding、微调和隐私能力选择模型；价格必须标注更新时间。
- 后端支持 Python CLI、独立服务、Docker 和其他部署方式；`npm start` 只负责 PC 联调快捷启动。
- 敏感数据必须使用认证加密；人物删除需要级联删除原始文件、解析数据、记忆、向量、会话和受控训练产物。

## 1. Product Summary

The product creates a personalized AI companion from a user-authorized corpus of historical conversations and media. A user first creates a persona, selects the person's relationship identity, imports up to 3 GiB of source data, reviews uncertain parsing results, and then chats through a model selected by capability and price.

The system must preserve the learned persona when the user changes models. It therefore uses a hybrid learning architecture: provider-independent style profiles, long-term memory, and retrieval-augmented generation for every model, with provider-specific fine-tuning only where a provider actually supports it.

Android and iOS applications built with Flutter are the long-term primary clients. The existing Web UI remains a PC development, data review, and administration client. The backend supports multiple launch modes; `npm start` is a PC development convenience and is not the canonical or only startup path.

## 2. Goals

- Import WeChat, QQ, native chat exports, common documents, images, audio, and supported backup formats.
- Support resumable, integrity-checked import jobs up to 3 GiB without buffering the full payload in process memory.
- Require a relationship identity before data import: father, mother, other relative, friend, romantic partner, or custom.
- Normalize every supported source into one versioned conversation schema.
- Learn text style, relationship context, long-term memories, and multimodal associations independently of the selected model.
- Let users compare and select models by provider, price, capability, context length, and privacy behavior.
- Support native adapters for DeepSeek, Xiaomi MiMo, Alibaba Cloud Model Studio/Qwen, OpenAI, Anthropic, Google Gemini, and Ollama.
- Support custom HTTP APIs, custom OpenAI-compatible endpoints, local runtimes, GGUF models, HuggingFace models, and a future provider plugin SDK.
- Encrypt sensitive data at rest and in transit, require explicit authorization before sending raw media to a third-party model, and provide complete data deletion.
- Replace simulated success, mock training results, unsafe file handling, and disconnected API paths with explicit, testable behavior.

## 3. Non-Goals For The First Delivery

- App Store or Play Store publication.
- Production billing, revenue settlement, or subscription management.
- Automatic decryption of proprietary encrypted WeChat or QQ databases without user-provided keys and a legally supported parser.
- Claiming that every model supports vision, embeddings, or fine-tuning.
- Uploading arbitrary local model binaries through the normal 3 GiB chat import endpoint.
- Training a 7B model locally on hardware that cannot satisfy the selected runtime's declared memory requirements.

Unsupported capabilities must return a typed error and never fall back to fabricated output.

## 4. System Architecture

```text
Flutter Android/iOS ----+
                        |
PC Web -----------------+--> API Gateway
                        |      |-- Authentication and authorization
CLI / Docker / npm -----+      |-- Persona service
                               |-- Resumable import service
                               |-- Parsing and normalization workers
                               |-- Consent and privacy service
                               |-- Hybrid learning service
                               |-- Conversation service
                               |-- Provider gateway
                               `-- Training job service

Storage adapters:
  Local development: SQLite + encrypted local object store
  Production: PostgreSQL + S3-compatible object store + KMS

Learning artifacts:
  Normalized messages -> style profile -> memories -> vector index
                                          `-> optional fine-tuning dataset/job
```

Each subsystem exposes a narrow interface so local-development storage and execution can later be replaced with production infrastructure without changing client contracts.

## 5. Client Architecture

### 5.1 Flutter Client

Flutter is the shared Android/iOS client. It owns:

- Account and session UI.
- Persona creation and relationship configuration.
- Native file/folder/media selection.
- Background chunk upload, pause, resume, retry, and network-change recovery.
- OS-backed secure storage for the application session token only.
- Import preview and correction workflows.
- Model catalog, capability comparison, price estimate, and model selection.
- Per-request third-party media consent.
- Streaming chat and conversation history.
- Data export, revocation, and deletion controls.

Provider API keys are never stored in Flutter.

### 5.2 PC Web Client

The Web client remains available for:

- Local PC debugging.
- Large desktop file and folder imports.
- Parsing preview and bulk correction.
- Provider and local-runtime administration.
- Job inspection and development diagnostics.

The Web client uses the same versioned API as Flutter. It does not contain a separate mock chat implementation.

## 6. Persona And Relationship Model

A persona must exist before an import session can be created.

Required persona fields:

- `persona_id`
- `owner_user_id`
- `display_name`
- `relationship_type`: `father`, `mother`, `relative`, `friend`, `partner`, or `custom`
- `relationship_label`: user-visible custom label
- `preferred_address`: how the persona addresses the user
- `user_address`: how the user addresses the persona
- `relationship_description`
- `tone_boundaries`
- `forbidden_topics`
- `created_at`, `updated_at`, and `schema_version`

Preset relationships supply editable defaults. Custom personas require a relationship label and description. Persona identity, memories, media, vector records, training artifacts, and conversations are partitioned by both owner and persona.

## 7. Import Sources And Parser Plugins

The import service selects parsers by content signature and manifest metadata, not file extension alone.

Initial parser families:

- WeChat exported text, HTML, supported SQLite databases, and backup packages.
- QQ exported text, HTML, supported databases, and backup packages.
- Generic TXT, JSON, JSONL, CSV, XML, and HTML conversation exports.
- DOCX and PDF documents containing conversations or transcripts.
- SQLite/DB files with recognized conversation schemas.
- ZIP archives containing a supported manifest and bounded file set.
- JPG, JPEG, PNG, WebP, HEIC, and supported animated-image formats.
- Common audio messages and voice recordings.
- Video messages as a later parser plugin using the same media contract.

Every parser implements:

```text
probe(source) -> confidence and source type
validate(source) -> validation result and required credentials
stream_records(source) -> normalized records and warnings
summarize() -> counts, unsupported items, and confidence distribution
```

Encrypted or unsupported native databases return an actionable `credentials_required` or `unsupported_format` result. They are never reported as successfully parsed with zero messages.

## 8. Resumable 3 GiB Upload Protocol

The default maximum aggregate size of one import job is 3 GiB (3,221,225,472 bytes). Administrators may lower or raise the limit through deployment configuration.

Upload flow:

1. Client creates an import session with persona ID, source manifest, file sizes, and optional whole-file SHA-256 values.
2. Server validates quota, identity, filenames, MIME hints, and aggregate size.
3. Server returns an upload ID, allowed chunk range, and already-received chunk indexes.
4. Client uploads chunks with an index, byte range, size, and SHA-256 checksum.
5. Server streams each chunk to encrypted temporary storage and records completion atomically.
6. Client may reconnect and request missing chunks.
7. Completion verifies the ordered manifest and total size before scheduling parsing.
8. Parsing streams from storage; it does not load the complete source into memory.

Default chunk size is 16 MiB. The server accepts a configured range of 4-64 MiB to accommodate mobile and desktop clients. Chunk submission is idempotent: a repeated matching chunk succeeds, while a repeated mismatched chunk returns a conflict.

Archive extraction enforces entry-count, expanded-size, nesting-depth, path-containment, and compression-ratio limits.

## 9. Normalized Conversation Schema

All parsers produce a versioned record rather than ad hoc `content` or `message` variants.

```json
{
  "schema_version": 1,
  "record_id": "stable-source-derived-id",
  "persona_id": "persona-id",
  "conversation_id": "source-conversation-id",
  "sender_id": "normalized-sender-id",
  "sender_name": "display sender",
  "sender_role": "persona|user|other|unknown",
  "message_type": "text|image|audio|video|file|system",
  "text": "normalized textual content",
  "timestamp": "ISO-8601 timestamp or null",
  "media_refs": [],
  "reply_to_record_id": null,
  "source": {
    "import_id": "import-id",
    "file_id": "file-id",
    "source_type": "wechat|qq|generic",
    "source_location": "non-sensitive logical location"
  },
  "confidence": 0.0,
  "review_state": "accepted|needs_review|rejected"
}
```

The user maps source participants to `persona`, `user`, or `other` before learning artifacts are built. Only records with an accepted mapping and review state are eligible for learning.

## 10. Multimodal Learning

The product uses full multimodal processing.

Chat screenshots:

- Detect chat bubbles, reading order, timestamps, sender side, text, emoji, and quoted replies.
- Produce individual normalized records with confidence scores.
- Require review when sender identity or order is uncertain.

Ordinary images and media:

- Extract people, scene, objects, events, approximate emotional cues, and links to surrounding conversation.
- Preserve a distinction between observed facts, model-generated descriptions, and user corrections.
- Generate searchable descriptions and embeddings only after the applicable consent and processing rules pass.
- Retain the encrypted original only according to the user's retention choice.

Raw media sent to an external provider requires a consent record containing provider, model, data category, estimated cost, purpose, timestamp, and authorization scope. Consent is requested before each new provider/scope combination and can be revoked for future processing. Revocation does not claim to erase data already processed by a third party; the UI must state the provider's applicable retention terms before authorization.

## 11. Hybrid Learning Architecture

Hybrid learning has four layers:

1. **Style profile:** message length, vocabulary, punctuation, emoji, cadence, emotional tendencies, preferred forms of address, and relationship-specific behavior. Only persona-authored messages contribute to imitation metrics.
2. **Long-term memory:** reviewed facts, events, relationships, preferences, and timelines extracted from accepted records.
3. **Retrieval:** relevant memories and source excerpts are selected per conversation under token, privacy, and recency budgets.
4. **Optional fine-tuning:** a target-role-only dataset is submitted only to adapters that advertise a compatible fine-tuning capability.

The prompt builder combines relationship rules, style profile, current conversation context, retrieved memory, and provider capability limits. It records which memories influenced a response for later debugging and user inspection.

Fine-tuning jobs must validate target speaker coverage, sample count, consent, provider capability, projected cost, and dataset integrity. A job is not successful until the provider or local runtime returns a verified artifact identifier and evaluation result.

## 12. Provider Gateway

The provider gateway owns model discovery, capability normalization, price metadata, invocation, streaming, embeddings, media analysis, cost estimation, rate-limit handling, and provider error translation.

Native adapters:

- OpenAI
- Anthropic Claude
- Google Gemini
- DeepSeek
- Xiaomi MiMo
- Alibaba Cloud Model Studio / Qwen
- Ollama
- Generic OpenAI-compatible endpoint

Provider interface:

```text
list_models()
get_model_capabilities(model_id)
estimate_cost(model_id, input_usage, requested_output)
chat(request)
stream_chat(request)
embed(request)
analyze_media(request)
get_fine_tuning_capabilities(model_id)
submit_fine_tuning(request)
get_training_job(provider_job_id)
cancel_training_job(provider_job_id)
health_check()
```

Unsupported methods return `capability_not_supported`. Provider-specific errors are translated to stable gateway error codes while preserving a redacted diagnostic payload.

### 12.1 Model Catalog And Pricing

Model records include provider, model ID, display name, input/output/media pricing units, context length, text/vision/audio/embedding/fine-tuning capabilities, regions, privacy metadata, and last price refresh time.

Prices are provider-supplied or administrator-configured data, not hard-coded client constants. The client displays the refresh timestamp and an estimate before expensive imports, media analysis, or fine-tuning. Final billed usage is recorded from provider usage responses when available.

Provider credentials may be platform-managed or supplied by an individual user (BYOK). Both forms are encrypted in the backend credential vault. A user-owned credential is scoped to its owner and provider and cannot be used by another user, administrator workflow, or background job without the owner's applicable authorization. The model catalog identifies whether pricing is platform-billed, provider-billed through BYOK, or local-compute-only.

### 12.2 Custom APIs And Local Models

Custom API registration supports:

- OpenAI-compatible base URL, authentication scheme, model ID, and optional custom headers.
- Generic HTTP adapters described by an administrator-installed provider plugin.
- Connectivity test, TLS validation, capability probe, and redacted error reporting before activation.

Local runtime registration supports Ollama, vLLM, llama.cpp, and future runtimes through the same provider contract. GGUF and HuggingFace models are registered by trusted artifact path or registry URI plus runtime parameters. Normal chat-data uploads cannot place executable model artifacts into a runtime directory.

Model records declare minimum memory, recommended accelerator, quantization, context limits, and supported modalities. The server rejects a local launch when measured resources do not satisfy the declared minimum.

The future provider plugin SDK exposes versioned contracts for authentication, model discovery, pricing, chat, streaming, embeddings, multimodal analysis, and fine-tuning. Plugins run under an allowlist and cannot receive unrelated persona data.

## 13. Security And Privacy

- Development servers bind to loopback by default. External binding requires explicit configuration.
- Production traffic requires TLS.
- Local development uses a local authenticated session; production uses OIDC/OAuth2-compatible login and scoped access tokens.
- Provider credentials are encrypted server-side and never returned to clients.
- Object encryption uses authenticated encryption with per-object data keys and envelope key management.
- Sensitive database fields, including message excerpts, persona descriptions, provider credentials, and model-generated memories, use field-level authenticated encryption or encrypted object references; SQLite and PostgreSQL must not become plaintext substitutes for object encryption.
- Local Windows development may protect the master key with DPAPI; production uses a KMS or injected secret provider.
- Path construction uses generated identifiers and resolved containment checks. User filenames are metadata, never filesystem paths.
- Request, archive, chunk, parser, and model limits are enforced before expensive work.
- Logs exclude raw messages, access tokens, provider keys, full local paths, and unredacted provider responses.
- Raw imports default to deletion 24 hours after successful normalization. Users may request earlier deletion.
- Normalized learning data has a configurable retention period with a hard policy maximum of five years.
- Persona deletion cascades to raw media, normalized records, corrections, profiles, memories, vectors, conversations, consent records where legally removable, and training artifacts controlled by the service.
- Audit records retain only the minimum metadata required to prove authorization and deletion actions.
- Anonymous usage analytics are opt-in, use a separate consent scope, and never contain raw messages, media, provider keys, persona descriptions, or stable source identifiers.

If authenticated encryption is unavailable, sensitive persistence fails closed. The system must not silently store plaintext.

## 14. API Surface

Initial versioned endpoints:

```text
POST   /api/v1/personas
GET    /api/v1/personas
GET    /api/v1/personas/{persona_id}
PATCH  /api/v1/personas/{persona_id}
DELETE /api/v1/personas/{persona_id}

POST   /api/v1/imports
GET    /api/v1/imports/{import_id}
GET    /api/v1/imports/{import_id}/missing-chunks
PUT    /api/v1/imports/{import_id}/files/{file_id}/chunks/{chunk_index}
POST   /api/v1/imports/{import_id}/complete
POST   /api/v1/imports/{import_id}/cancel
GET    /api/v1/imports/{import_id}/preview
POST   /api/v1/imports/{import_id}/corrections
POST   /api/v1/imports/{import_id}/participant-mapping

POST   /api/v1/consents
POST   /api/v1/consents/{consent_id}/revoke

GET    /api/v1/providers
GET    /api/v1/models
POST   /api/v1/providers/custom
POST   /api/v1/providers/{provider_id}/test

POST   /api/v1/conversations
GET    /api/v1/conversations/{conversation_id}
POST   /api/v1/conversations/{conversation_id}/messages
GET    /api/v1/conversations/{conversation_id}/stream

POST   /api/v1/training-jobs
GET    /api/v1/training-jobs/{job_id}
POST   /api/v1/training-jobs/{job_id}/cancel

GET    /api/v1/data-export
POST   /api/v1/data-deletion
GET    /api/v1/health
```

Streaming chat uses Server-Sent Events initially because it works consistently for Web, Flutter, proxies, and resumable HTTP infrastructure. WebSocket support can be added later without replacing the message contract.

## 15. Job State And Error Handling

Long-running imports, parsing, media analysis, embedding, deletion, and fine-tuning use:

```text
pending -> running -> completed
                   -> failed
                   -> cancelled
```

Jobs record progress, retryable/non-retryable classification, redacted error code, user-facing message, and diagnostic correlation ID. Completion requires durable output verification.

Representative HTTP behavior:

- `400` invalid request syntax
- `401/403` unauthenticated or unauthorized access
- `409` conflicting chunk, state transition, or idempotency key
- `413` configured upload limit exceeded
- `415` unsupported media type
- `422` parse, mapping, validation, or capability error
- `428` explicit consent required
- `429` provider or application rate limit
- `502` invalid upstream provider response
- `503` provider, model, encryption, or worker unavailable

## 16. Service Launch Modes

The backend must support:

- `python -m companion_server`
- An installed `companion-server` CLI entry point
- A direct development script
- Docker Compose for backend and production-style dependencies
- `npm start` as a wrapper that launches the PC development configuration

All modes use the same configuration model and API. No launch mode owns unique business logic.

## 17. Delivery Phases

### Phase 1: Safe Backend Foundation And PC Loop

- Establish versioned contracts, built-in test runner, configuration, and multiple launch modes.
- Replace unsafe static path resolution, raw filename usage, mock chat, and false training success.
- Normalize text/JSON imports and target-person mapping.
- Add a provider gateway contract with a deterministic test provider and explicit unconfigured-provider behavior.
- Connect the Web client to the real API.

### Phase 2: Large Import And Data Governance

- Add 3 GiB resumable chunk uploads and encrypted object storage.
- Add parser plugins for WeChat, QQ, common documents, databases, archives, images, and audio.
- Add preview, confidence review, corrections, participant mapping, retention, export, and deletion.

### Phase 3: Provider And Hybrid Learning

- Add native cloud and local provider adapters.
- Add model catalog, current pricing metadata, cost estimates, style profile, memory extraction, vector retrieval, and per-provider multimodal consent.
- Add capability-gated fine-tuning jobs.

### Phase 4: Flutter Applications

- Build Android/iOS authentication, persona, import, background upload, review, model selection, consent, chat, and privacy-management flows.
- Validate recovery across process termination, network changes, and operating-system background limits.

### Phase 5: Production Platform

- Add PostgreSQL, S3-compatible storage, KMS, distributed workers, multi-user isolation, audit operations, billing, monitoring, and store-release preparation.

## 18. Testing Strategy

The repository uses tests that run in the checked-in development environment without relying on demo scripts.

- Unit tests for path containment, identifiers, schema validation, parser probes, normalization, participant mapping, relationship presets, style extraction, consent rules, capability checks, and price estimates.
- Contract tests shared by every provider adapter.
- Integration tests for resumable uploads, duplicate chunks, checksum mismatch, cancellation, encrypted persistence, parsing, preview, correction, deletion, and API authorization.
- Security regression tests for directory traversal, archive traversal, oversized bodies, malformed JSON, XSS-safe rendering, unauthorized persona access, and credential redaction.
- Streaming tests that prove large payloads do not scale process memory with total file size.
- Browser tests for the PC import, review, provider selection, consent, and chat workflows.
- Flutter tests for state restoration, secure token storage, chunk resume, background retry, and data deletion.

A 3 GiB acceptance test uses generated streaming data or a sparse fixture so CI does not need to retain a 3 GiB artifact. A dedicated local stress test verifies end-to-end upload and bounded memory.

## 19. Acceptance Criteria

- A persona and relationship identity are required before import creation.
- A 3 GiB import can pause, resume, verify, parse, and complete without full-body memory buffering.
- Traversal requests cannot read or write outside configured roots.
- WeChat, QQ, generic text, documents, databases, and media either normalize successfully or return an explicit actionable status.
- Persona and user messages are never mixed as fine-tuning targets.
- Raw third-party media is never transmitted without a valid consent record.
- Model switching preserves persona profile and memory.
- The model catalog exposes capability and price-refresh metadata.
- DeepSeek, Xiaomi MiMo, Qwen, OpenAI, Anthropic, Gemini, Ollama, generic endpoints, and custom/local registrations fit the same provider contract.
- Missing providers, models, encryption, or training capability cause explicit failure rather than mock output.
- Persona deletion removes all controlled derived data and reports any external-provider deletion limitation.
- Python CLI, direct development mode, Docker mode, and the npm wrapper execute the same backend behavior.
- The PC Web client performs real API calls and renders user/model content without DOM injection.

## 20. Existing Project Migration

- Preserve the current Web layout initially, but replace browser-generated mock replies with the versioned conversation API.
- Replace the three divergent server implementations with one backend package; keep temporary compatibility launchers only while tests prove equivalent startup behavior.
- Quarantine existing files under `data/uploaded` until the owner explicitly imports or deletes them. Do not silently treat them as valid training data.
- Convert legacy `sender/message` and `sender/content` records through an explicit migration adapter into schema version 1.
- Replace conceptual encryption with authenticated encryption and migrate only data that can be decrypted and attributed safely.
- Replace fixed training metrics with capability-gated real jobs or an explicit unavailable result.
- Split runtime, optional model, provider, and development dependencies so PC API testing does not require downloading a full training stack.
- Update the import guide and privacy policy to match actual size limits, retention, encryption, third-party consent, model selection, and deletion behavior before any public release.
