# 个人化风格情感伴侣 AI (Personalized Style Companion AI)

## 项目概述

开发一个能够高度模仿特定人物性格、语气、说话风格、惯用词汇的聊天机器人。

当前里程碑提供安全的本地开发闭环：先创建人物身份，再通过可恢复分片上传导入资料，并从统一供应商目录选择模型。架构与后续移动端、混合学习和生产化路线见 `docs/superpowers/specs/2026-07-30-personalized-companion-platform-design.md`。

## 核心功能

1. **风格模仿**：AI必须学习和复现聊天记录中目标人物的语气、情绪起伏、常用口头禅、回复长度以及标点符号和表情符号的使用习惯。
2. **上下文记忆**：支持多轮对话，能够记忆并引用近5轮的上下文信息，确保回复连贯性。

## 技术要求

- 基于LLM（大型语言模型）架构
- 使用历史聊天记录进行微调(Finetuning)
- 严格保证数据隐私安全

## 目录结构

```
personalized-companion-ai/
├── data/                  # 数据相关
│   ├── raw/               # 原始聊天记录
│   ├── processed/         # 处理后的数据
│   └── datasets/          # 训练数据集
├── models/                # 模型相关
│   ├── base/              # 基础模型
│   ├── finetuned/         # 微调后的模型
│   └── configs/           # 模型配置
├── src/                   # 源代码
│   ├── preprocessing/     # 数据预处理模块
│   ├── training/          # 训练模块
│   ├── inference/         # 推理模块
│   └── api/               # API服务
├── tests/                 # 测试相关
├── utils/                 # 工具函数
├── docs/                  # 文档
├── requirements.txt       # 默认核心依赖入口
├── requirements-core.txt  # 核心服务依赖
├── requirements-parsers.txt # 解析与文本处理依赖
├── requirements-models.txt  # 模型与训练依赖
├── requirements-dev.txt     # 开发测试依赖
└── README.md             # 项目说明
```

## 依赖安装

默认安装只包含轻量核心服务，不会安装 `torch`、`transformers` 或 `datasets`：

```powershell
python -m pip install -r requirements.txt
```

按实际用途选择一个扩展清单。解析组自动包含核心组，模型组自动包含解析组和核心组：

```powershell
# 聊天文件解析、数值、文本处理和本地图像元数据检测
python -m pip install -r requirements-parsers.txt

# 模型与训练，同时包含解析和核心依赖
python -m pip install -r requirements-models.txt

# 完整开发测试工具（包含本地解析与媒体检查依赖）
python -m pip install -r requirements-dev.txt
```

当前正式入口 `python -m src.server` 使用轻量核心依赖，并通过 `cryptography` 提供 AES-GCM 认证加密。训练模块仍会在真实训练器和数据集缺失时明确返回不可用，不会因为安装模型依赖而生成模拟成功结果。

## 本地启动

Python 是服务的正式入口，npm 和 PowerShell 只是 PC 调试包装：

```powershell
python -m src.server
npm start
.\scripts\run_server.ps1
```

默认地址为 `http://127.0.0.1:8080`。运行测试：

```powershell
python -m unittest discover -s tests -p "test*.py" -v
```

对象字节存储通过 `PAST_PARTNER_STORAGE_BACKEND` 选择，默认值为 `local`，继续使用当前 `<data-dir>` 本地布局。P4-01 目前只实现本地适配器；配置 `s3`、`minio` 或其他值会在启动校验阶段明确失败，不会静默回退到本地。加密元数据通过 `PAST_PARTNER_METADATA_BACKEND` 选择，默认值为 `sqlite`，当前所有仓储共享一个 SQLite 适配器和同一迁移账本；配置 `postgres`、`postgresql` 或其他值会在写入前明确失败。PostgreSQL、云对象存储和 KMS 属于后续独立任务，当前不需要云凭据或额外 SDK。

移动端开发联调仍由 Python 服务负责启动。默认回环 HTTP 只适合本机浏览器和模拟器；若需要让真机访问，必须在开发模式同时配置 `PAST_PARTNER_DEV_DEVICE_BOOTSTRAP_TOKEN`、`PAST_PARTNER_DEV_DEVICE_ALLOWED_NETWORKS`、`PAST_PARTNER_DEV_DEVICE_TLS_CERT_FILE` 和 `PAST_PARTNER_DEV_DEVICE_TLS_KEY_FILE`。服务会校验私有 IPv4/IPv6 ULA 地址、证书 IP SAN 和 TLS 1.2+，并自动使用 `https://` 启动。设备通过 `X-Dev-Device-Bootstrap-Token` 仅初始化最多 1 小时的设备会话；`X-Local-Owner-Token` 仍只用于生产 owner 引导，两者不会互相替代。允许网段优先使用 `/32` 或 `/128`，不得配置公网、回环、未指定地址或 catch-all 网段。不要把真实 token、证书或私钥提交到仓库。

模拟器或 Android ADB reverse/port-forward 联调可以继续使用回环 HTTP，不发送设备配对 header；真机直连才需要受控私有 LAN TLS 配置。

移动端代码位于 `mobile/`，包含端点白名单、设备配对会话、Secure Storage 会话恢复，以及简化/活泼两套静态对话预览。生成 Android/iOS runner、执行 `flutter pub get`、`flutter analyze`、`flutter test` 和 APK 构建前，需要先安装 Flutter SDK；未安装 SDK 的环境不能将该目录宣称为已构建的 Android/iOS 应用。

P3-05 增加移动端导入任务的进程重启恢复：上传开始后，Secure Storage 只保存导入任务 ID、人物 ID、文件显示元数据和系统文件引用，不保存访问令牌、文件正文或完整路径到页面/日志。网络失败或应用进程被终止后，重新进入未完成任务会优先使用恢复清单继续缺片上传；清单缺失或文件已不可读时会明确提示重新选择原文件。上传完成不等同于操作系统级后台任务，当前仍由用户重新打开应用触发恢复。APK 使用 `scripts/build_mobile_apk.ps1` 生成，产物命名为 `Past-partner_<版本>_<yyyyMMdd_HHmm>_<debug|release>.apk`，输出目录只保留最新一组命名 APK。

P3-06 增加移动端导入预览与审核：在上传完成后读取有限的规范化预览，支持参与者身份映射、记录状态修正和失败重试；客户端不解析原始微信/QQ 文件，也不保存原始正文。

P3-07 增加移动端模型目录与选择：通过 `/api/v1/models` 展示供应商、能力、上下文、隐私标签和价格更新时间，按供应商筛选并将选择结果返回人物工作区；通过 `/api/v1/models/cost-estimate` 使用服务端价格估算，未配置价格时明确提示不可估算。移动端不接收或保存供应商 API Key，模型切换持久化和聊天请求属于后续任务。

P3-08 增加移动端第三方处理授权管理：在当前人物下查看已生效/已撤回的图片、音频、视频授权，创建授权时精确绑定已选供应商、模型、数据类别、用途、作用域和预计费用上限，支持二次确认撤回。客户端不读取或上传媒体正文、不保存供应商 API Key；授权撤回只阻止后续处理，不代表第三方已接收的数据会自动删除。

P3-09 增加移动端真实聊天与会话历史：会话使用 owner/persona 双重边界并通过认证加密 SQLite 保存，提供创建、列表、读取和发送接口；Flutter 聊天页恢复当前人物与已选模型的最近会话，展示用户/对方消息气泡、发送中状态、provider 失败后的重试入口，并支持简洁/活泼两套对话外观。当前任务只覆盖非流式文本聊天；SSE 流式、语音/图片/视频等动作和隐私管理继续按后续独立任务推进。

P3-10 增加移动端隐私管理：人物工作区提供隐私入口，可读取 owner 级导出摘要并展示人物、导入、授权、训练任务和会话数量；摘要明确排除原始导入载荷、第三方供应商数据和审计记录，不把完整导出内容或访问令牌保存到客户端状态。人物删除必须二次确认，并调用 `DELETE /api/v1/personas/{persona_id}` 级联清理本地受控导入、授权、训练任务和会话；删除后自动刷新人物列表。第三方已经接收的数据仍需按供应商规则单独处理。

导入任务默认允许最多 3 GiB（3,221,225,472 字节）。该限制按一次任务中所有文件大小之和计算；可通过 `PAST_PARTNER_MAX_IMPORT_BYTES` 在服务端调整，单文件不能绕过任务总量限制。

P1-03 已增加通用 HTML 聊天记录解析，支持常见消息容器、发送者/时间/正文标记、HTML 实体、UTF-8/UTF-16/GB18030 编码，并忽略脚本、样式和模板内容。

原始任务编号对齐：ZIP 安全边界已随 P0-32/P0-33 备份解析落地，对应原 P1-04；通用 SQLite schema 探测对应原 P1-05；微信/QQ 原生数据库目录解析对应原 P1-06/P1-07。当前 P1-07 已补充支持 `.db`、`.sqlite`、`.sqlite3` 及其 WAL/SHM sidecar，并保持只读快照、未知 schema 和密钥需求的明确错误。
附件引用元数据标准化是额外补充能力（不占用原始 P1-05 编号）：JSON/JSONL、CSV、XML、HTML 和通用 SQLite 消息中的图片、音频、视频、文件、贴纸引用会统一为安全的相对路径或 URL、文件名、MIME、类型、大小和可选校验值；绝对路径、路径穿越、内嵌原始字节会被拒绝，解析器不会读取媒体内容。
原 P1-08 DOCX 对话文档解析已独立收口：只读取受控 `word/document.xml`，保留段落内换行和制表符，并对损坏、超限、不安全归档和无对话内容返回明确错误。原 P1-09 PDF 对话文档解析现已独立收口：优先使用可选 `pypdf`，并保留有界纯文本回退，支持常见文本操作符和 UTF-16 十六进制文本；加密、页数超限、损坏和无对话内容会返回明确错误。文档扩展名优先于通用文本探测。
P1-10 已增加第三方媒体处理授权记录：授权按人物、供应商、模型、数据类别和作用域精确匹配，记录使用 AES-GCM 加密保存，支持 owner 级查询、撤回和人物删除级联清理。原始媒体只在存在有效授权时才允许后续第三方处理；本地上传、解析或保存媒体引用不等于同意向第三方发送。当前任务只提供授权生命周期，不触发 OCR、ASR、视觉分析或真实媒体模型调用。
P2-01 已扩展模型目录元数据，提供能力、上下文长度、区域、隐私标签、结构化价格和价格刷新时间；可通过 `PAST_PARTNER_MODEL_PRICING_JSON` 配置供应商/管理员价格。`POST /api/v1/models/cost-estimate` 按输入/输出 token 和媒体单位返回可复核估算，未配置价格时明确返回 `pricing_unavailable`，不生成伪造成本。价格是估算值，不替代供应商最终账单。
P2-02 已补齐统一 Provider 构建入口：OpenAI、DeepSeek、小米 MiMo、阿里千问、Ollama 和自定义 OpenAI-compatible 继续使用兼容协议；Anthropic Messages 与 Google Gemini `generateContent` 使用原生请求/响应适配器。所有网络调用都经过统一 JSON 传输边界，未配置凭据时仍返回 `provider_not_configured`；本任务不实现流式、Embedding、媒体分析或微调。

P2-03 已增加 provider-independent 风格画像提取：解析器输出先统一为 `NormalizedMessage`，再只使用人物发送者的文本统计消息长度、词汇、标点、表情、节奏、情绪倾向、偏好称呼和关系行为。`ChatDataParser.generate_style_profile` 可复用现有解析器注册表；画像不携带原始正文，不调用供应商，当前仅在调用内存中生成，尚未持久化或提供独立画像 API。

P2-04 已增加本地长期记忆候选提取：从规范化消息生成事实、事件、关系、偏好和时间线候选，支持限定已接受的 `record_id`，对重复证据合并，并为每条候选提供稳定 ID、有限证据文本、来源、时间、置信度和 `needs_review` 审核状态。`ChatDataParser.generate_long_term_memory` 不调用模型、不上传原始内容；审核通过前候选不会被视为事实，当前尚未持久化或提供独立记忆 API。

P2-05 已增加 provider-independent `VectorMemoryRetriever`：对已审核为 `accepted` 的长期记忆候选执行确定性稀疏向量检索，默认只允许 `persona`/`user` 说话人范围，并按候选数、token 总量和可选时间窗口限制结果。结果只返回有限证据文本、稳定记忆 ID、来源记录 ID、排序分数和排除计数；原始查询只保留 SHA-256 指纹，不调用 embedding 或聊天供应商，也不持久化向量索引。

P2-06 已增加多模态能力门控：`POST /api/v1/consents/{consent_id}/authorize` 在媒体发送或处理前同时核对活动授权、供应商/模型/数据范围，以及目录声明的 `vision`、`audio` 或 `video` 能力。能力不匹配时明确拒绝；该接口只返回授权决定和能力证据，不上传媒体、不替代供应商隐私承诺。

P2-07 已增加能力门控微调任务：`POST /api/v1/training-jobs/estimate` 只在本地短暂构建并清除受限 JSONL，返回样本量、摘要和价格；创建任务要求独立的 `persona_text`、`fine_tuning`、`fine_tuning:{import_id}` 精确授权，且同一份成本授权只能提交一次。数据集只包含已接受的 `persona` 文本，用户、其他参与者、未审核或已拒绝记录不会作为目标样本。任务元数据（状态、进度、可重试性、诊断 ID、摘要、成本、Provider 工件和评测）以 AES-GCM 加密保存，不保存正文、临时路径、凭据或完整 Provider 响应；`GET /api/v1/training-jobs`、`GET /api/v1/training-jobs/{job_id}` 与 `POST /api/v1/training-jobs/{job_id}/cancel` 提供 owner 范围状态管理。外发前会加密保存 `submission_started` 意图；任何微调适配器都必须能按本地 job ID 对账，才能在 Provider 已接受而远端 ID 持久化失败后继续查询或取消。`local_cleanup_failure_code` 独立记录临时明文清理故障，不会把已验证的 Provider 完成结果改写为 `failed`。只有 Provider 返回非空工件 ID 和评测对象才会标记 `completed`。当前真实供应商适配器尚未声明 `fine_tuning` 能力，开发和生产环境会明确拒绝；确定性 Provider 仅在 `PAST_PARTNER_MODE=test` 下用于自动化合同测试，不能视为实际模型训练。

媒体处理实测补充（不占用原路线图任务编号）：已完成上传的图片、音频或视频可调用 `GET /api/v1/imports/{import_id}/media-inspection` 获取本地验证的格式元数据。当前图片格式映射为 BMP、GIF、ICO、JPEG、PNG、TIFF、WebP；音频格式映射为 Ogg、WAV、MP3；视频格式映射为 WebM、MP4（其他格式会明确拒绝，不伪造成功）。图片返回格式和尺寸；音频/视频返回格式、时长、编码、采样率或画面尺寸。该接口仅在服务端受控临时路径中处理单个文件边界，响应不包含原始字节或本地路径，且明确返回 `provider_transfer: false`。它不执行 OCR、ASR、图片/视频语义理解，也不调用模型；图片检测需要 `requirements-parsers.txt` 中的 Pillow，音频和视频检测还需要本机 `ffprobe` 位于 `PATH`。

断点续传可通过 `GET /api/v1/imports/{import_id}/missing-chunks?expected_chunks=N` 查询已接收和缺失的分片索引。
导入进度可通过 `GET /api/v1/imports/{import_id}/progress` 查询服务端确认的字节数、分片索引和百分比。
错误响应统一返回稳定 `error.code` 和 UUID 格式的 `error.diagnostic_id`；诊断 ID 同时写入服务端日志，便于在不暴露内部异常细节的情况下定位请求。
已完成的导入可通过 `GET /api/v1/imports/{import_id}/preview?limit=20` 查看解析摘要和有限条规范化消息；多文件导入按清单顺序逐文件解析，并在记录中返回文件来源；`limit` 最大为 100。
已完成的导入可通过 `POST /api/v1/imports/{import_id}/participant-mapping` 保存参与者角色映射，再通过同路径 `GET` 回读；角色仅支持 `persona`、`user`、`other` 和 `unknown`，映射内容随导入清单加密保存。
预览记录会带有稳定 `record_id` 和 `review_state`；可通过 `POST /api/v1/imports/{import_id}/corrections` 提交单文件或多文件预览中的字段修正及 `accepted`、`needs_review` 或 `rejected` 状态，修正同样写入加密导入清单并在后续预览中回显。
可通过 `DELETE /api/v1/imports/{import_id}` 删除单个导入及其加密分片、合并对象和清单；该操作按当前 owner 校验，删除后导入接口返回 404。
可通过 `DELETE /api/v1/personas/{persona_id}` 删除人物及其 owner 名下的全部导入任务、加密分片、合并对象和清单；删除后人物和关联导入接口返回 404。
可通过 `GET /api/v1/data-export` 获取当前 owner 的版本化人物、导入任务和加密清单元数据；原始导入载荷、第三方供应商数据和审计记录会在导出范围中明确标记为未包含。
可通过 `PAST_PARTNER_RAW_RETENTION_SECONDS` 启用启动时保留期清理；正数表示清理当前 owner 名下更新时间早于阈值且状态为 `failed` 或 `cancelled` 的导入及其加密对象，默认 `0` 关闭。由于当前尚未记录成功标准化事件，`uploaded`、`processing` 和 `completed` 导入不会被该策略自动删除。

P0-18 已加入基于内容探测的通用解析器注册表；P0-19 的标准化消息现在包含服务端稳定 `record_id`，并在解析阶段生成后供预览、修正和后续持久化复用；P0-20 的 TXT 解析支持常见时间/发送者格式、多行消息、UTF-8/UTF-16 编码和无时间发送者行；P0-21 的 JSON/JSONL 解析统一支持 UTF-8/UTF-16 编码并保持 JSONL 流式读取；P0-22 的导入预览按多文件清单边界逐文件解析并聚合有限记录；P0-25 增加微信 TXT/HTML 导出解析；P0-26 增加 QQ TXT/HTML 导出解析；P0-27 增加默认关闭、启动时执行的终态原始导入保留期清理；P0-28 提供 owner 级版本化数据导出，包含人物、导入任务、参与者映射、预览修正和加密清单元数据，并明确排除原始载荷；P0-29 提供 owner 级人物删除级联清理，并拒绝会造成部分删除的处理中任务；P0-30 增加微信 3.x/4.x 明文 SQLite 数据库目录解析；P0-31 增加 QQ 通用消息表明文 SQLite 数据库目录解析；P0-32 增加带 manifest v1 的微信 ZIP 备份包安全解析；P0-33 增加带 manifest v1 的 QQ ZIP 备份包安全解析；P1-01 增加通用 CSV 聊天记录解析；P1-02 增加通用 XML 聊天记录解析；P1-03 增加通用 HTML 解析；P1-04 增加通用 SQLite schema 自动探测；P1-05 增加跨格式附件引用元数据标准化；P1-08 增加 DOCX 对话文本解析；P1-09 增加 PDF 对话文本解析；P1-10 增加第三方媒体处理授权记录、精确作用域校验和撤回接口；P2-01 增加模型能力、上下文、隐私和可刷新价格元数据及成本估算接口。数据库解析仅接受用户主动选择的目录，先形成包含现有 WAL/SHM 的一致只读快照，再识别已支持 schema；单个 `.db`、加密库和未知 schema 会返回明确错误。私有加密备份、媒体内容分析、浏览器目录聚合和第三方模型处理仍按后续任务推进；媒体原始内容不会因本地上传而自动发送给第三方。

服务启动时会在 `<data-dir>/database/past-partner.sqlite3` 创建本地 SQLite 数据库，并在同一事务中执行尚未应用的版本化迁移。已执行版本记录在 `schema_migrations` 表中，重复启动不会重复应用；迁移历史不一致或迁移失败时，服务会停止启动而不是继续使用不确定的结构。

应用已装配统一主密钥提供器。所有模式都优先读取 `PAST_PARTNER_MASTER_KEY`，其值必须是严格 Base64 编码的 32 字节随机密钥；生产模式缺失或配置错误时，后续敏感写入取钥会直接失败。Windows 本地开发模式未配置环境密钥时，会在首次取钥时生成随机密钥，并通过当前 Windows 用户的 DPAPI 保护后写入 `<data-dir>/secrets/master-key.dpapi`。DPAPI 文件不能跨 Windows 用户直接解保护，不应作为备份密钥使用。

P0-05 提供版本化 AES-256-GCM 信封加密服务；P0-06 已将上传分片和合并对象接入该服务；P0-07 已将人物名称、关系等内容字段迁入加密 SQLite 仓储，P0-08 又将导入任务和上传清单迁入同一事务仓储，P0-09 增加本地 owner Bearer 会话并为人物、导入和上传接口执行 owner 归属校验，随机服务端 ID 仅作为非秘密索引。每个分片、人物记录、导入任务和清单记录使用独立随机数据密钥和 nonce，AAD 绑定对象身份；3 GiB 导入始终按有界分片处理。启动时会先加密迁移旧 `personas/*.json`、`imports/*.json` 和 `upload-manifests/*.json`，提交成功后才删除明文源文件。开发模式只允许回环地址初始化会话，生产模式需配置 `PAST_PARTNER_OWNER_BOOTSTRAP_TOKEN`；OIDC/OAuth2、多用户账户和审计属于后续任务，具体限制见 `docs/privacy_policy.md`。

模型供应商需要在服务端显式配置凭据和允许的模型。未配置时接口返回 `provider_not_configured`，不会生成模拟回复。微调能力同样遵循目录能力、适配器、精确训练授权和价格检查，不会返回伪造训练指标；在接入并验证真实供应商微调适配器前，默认目录不会把现有模型标记为可训练。

P2-07 的媒体检测 `provider_transfer` 标记不适用于训练任务。只有目录 `fine_tuning` 能力、价格、配置的微调适配器和独立训练授权都满足时，Provider 网关才会接收训练文本；缺少能力会返回 `capability_not_supported`，绝不回退为聊天请求。任务只有收到非空 `artifact_id` 与非空 `evaluation` 后才进入 `completed`，两者仅是有限结果元数据。

模型价格和附加元数据通过 `PAST_PARTNER_MODEL_PRICING_JSON` 由部署者维护，格式见 `.env.example`；服务会在 `/api/v1/models` 返回刷新时间，并通过 `/api/v1/models/cost-estimate` 提供估算。未配置价格的模型仍可展示能力，但不能生成成本估算。

OpenAI、DeepSeek、小米 MiMo、阿里千问、Anthropic、Gemini、Ollama 与自定义 OpenAI-compatible 接口的环境变量模板见 `.env.example`。模板只用于列出变量名，服务不会从前端接收或返回 API Key。自定义 HTTP 仍需后续插件 SDK，不会因为目录可见而伪造可用状态。
