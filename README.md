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
# 聊天文件解析、数值和文本处理
python -m pip install -r requirements-parsers.txt

# 模型与训练，同时包含解析和核心依赖
python -m pip install -r requirements-models.txt

# 核心服务的开发测试工具
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

导入任务默认允许最多 3 GiB（3,221,225,472 字节）。该限制按一次任务中所有文件大小之和计算；可通过 `PAST_PARTNER_MAX_IMPORT_BYTES` 在服务端调整，单文件不能绕过任务总量限制。

P1-03 已增加通用 HTML 聊天记录解析，支持常见消息容器、发送者/时间/正文标记、HTML 实体、UTF-8/UTF-16/GB18030 编码，并忽略脚本、样式和模板内容。

原始任务编号对齐：ZIP 安全边界已随 P0-32/P0-33 备份解析落地，对应原 P1-04；通用 SQLite schema 探测对应原 P1-05；微信/QQ 原生数据库目录解析对应原 P1-06/P1-07。当前 P1-07 已补充支持 `.db`、`.sqlite`、`.sqlite3` 及其 WAL/SHM sidecar，并保持只读快照、未知 schema 和密钥需求的明确错误。
附件引用元数据标准化是额外补充能力（不占用原始 P1-05 编号）：JSON/JSONL、CSV、XML、HTML 和通用 SQLite 消息中的图片、音频、视频、文件、贴纸引用会统一为安全的相对路径或 URL、文件名、MIME、类型、大小和可选校验值；绝对路径、路径穿越、内嵌原始字节会被拒绝，解析器不会读取媒体内容。
原 P1-08 DOCX 对话文档解析已独立收口：只读取受控 `word/document.xml`，保留段落内换行和制表符，并对损坏、超限、不安全归档和无对话内容返回明确错误。原 P1-09 PDF 对话文档解析现已独立收口：优先使用可选 `pypdf`，并保留有界纯文本回退，支持常见文本操作符和 UTF-16 十六进制文本；加密、页数超限、损坏和无对话内容会返回明确错误。文档扩展名优先于通用文本探测。
P1-10 已增加第三方媒体处理授权记录：授权按人物、供应商、模型、数据类别和作用域精确匹配，记录使用 AES-GCM 加密保存，支持 owner 级查询、撤回和人物删除级联清理。原始媒体只在存在有效授权时才允许后续第三方处理；本地上传、解析或保存媒体引用不等于同意向第三方发送。当前任务只提供授权生命周期，不触发 OCR、ASR、视觉分析或真实媒体模型调用。
P2-01 已扩展模型目录元数据，提供能力、上下文长度、区域、隐私标签、结构化价格和价格刷新时间；可通过 `PAST_PARTNER_MODEL_PRICING_JSON` 配置供应商/管理员价格。`POST /api/v1/models/cost-estimate` 按输入/输出 token 和媒体单位返回可复核估算，未配置价格时明确返回 `pricing_unavailable`，不生成伪造成本。价格是估算值，不替代供应商最终账单。

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

模型供应商需要在服务端显式配置凭据和允许的模型。未配置时接口返回 `provider_not_configured`，不会生成模拟回复。微调能力同样遵循真实能力检查，不会返回伪造训练指标。

模型价格和附加元数据通过 `PAST_PARTNER_MODEL_PRICING_JSON` 由部署者维护，格式见 `.env.example`；服务会在 `/api/v1/models` 返回刷新时间，并通过 `/api/v1/models/cost-estimate` 提供估算。未配置价格的模型仍可展示能力，但不能生成成本估算。

DeepSeek、小米 MiMo、阿里千问、Ollama 与自定义 OpenAI-compatible 接口的环境变量模板见 `.env.example`。模板只用于列出变量名，服务不会从前端接收或返回 API Key。
