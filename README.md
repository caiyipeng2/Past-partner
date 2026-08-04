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

服务启动时会在 `<data-dir>/database/past-partner.sqlite3` 创建本地 SQLite 数据库，并在同一事务中执行尚未应用的版本化迁移。已执行版本记录在 `schema_migrations` 表中，重复启动不会重复应用；迁移历史不一致或迁移失败时，服务会停止启动而不是继续使用不确定的结构。

应用已装配统一主密钥提供器。所有模式都优先读取 `PAST_PARTNER_MASTER_KEY`，其值必须是严格 Base64 编码的 32 字节随机密钥；生产模式缺失或配置错误时，后续敏感写入取钥会直接失败。Windows 本地开发模式未配置环境密钥时，会在首次取钥时生成随机密钥，并通过当前 Windows 用户的 DPAPI 保护后写入 `<data-dir>/secrets/master-key.dpapi`。DPAPI 文件不能跨 Windows 用户直接解保护，不应作为备份密钥使用。

P0-05 已提供版本化 AES-256-GCM 信封加密服务：每个对象使用独立随机数据密钥和 nonce，数据密钥再由 P0-04 的主密钥认证加密；信封携带已认证的非秘密主密钥标识，部署方可注入按标识解析历史密钥的函数以支持密钥轮换。调用方必须提供关联数据（AAD）绑定对象身份。单次加密默认限制为 64 MiB，不能把完整的 3 GiB 导入一次性读入内存；P0-06 必须按有界分段加密，并将对象 ID、分段序号和结束标记纳入 AAD。当前 JSON、上传分片与合并文件仍是未加密格式；将这些敏感写入切换到加密对象存储属于后续 P0-06，在完成前仍以 `docs/privacy_policy.md` 的限制为准。

模型供应商需要在服务端显式配置凭据和允许的模型。未配置时接口返回 `provider_not_configured`，不会生成模拟回复。微调能力同样遵循真实能力检查，不会返回伪造训练指标。

DeepSeek、小米 MiMo、阿里千问、Ollama 与自定义 OpenAI-compatible 接口的环境变量模板见 `.env.example`。模板只用于列出变量名，服务不会从前端接收或返回 API Key。
