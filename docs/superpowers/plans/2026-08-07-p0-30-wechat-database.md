# P0-30 微信数据库安全解析实现记录

## 目标

对照原始产品大纲，为用户主动选择的微信数据库目录提供可复用的明文 SQLite 解析入口，输出项目统一的 `NormalizedMessage`。该任务不承担模型训练、画像生成、浏览器多文件聚合或自动密钥提取。

## 已实现边界

- 只接受目录来源；单个 `.db` 文件返回 `source_not_directory`。
- 复制所有 `.db` 以及存在的 `-wal`、`-shm` sidecar，并在复制前后比较文件清单、大小和修改时间；持续变化返回 `snapshot_changed`。
- 快照内通过 SQLite read-only URI 和 `PRAGMA query_only=ON` 查询，源目录不写入。
- 识别微信 3.x `MicroMsg.db` + `MSG*.db` 和微信 4.x `contact/`、`message/` 目录的已知表结构。
- 微信 4.x 要求完整 `chat_id`；不能用昵称猜测会话。
- 非 SQLite 文件头、加密库和未知 schema 返回 `encrypted_database` 或 `unsupported_schema`，不猜测或持久化密钥。
- 当前浏览器上传器仍按文件建立独立导入任务；目录聚合需要后续上传编排任务，不在本 P0-30 核心解析范围内。

## 验收证据

`tests/unit/test_wechat_database.py` 覆盖明文 3.x/4.x 消息标准化、稳定记录 ID、单文件拒绝、加密库拒绝、WAL/SHM 快照和源变化重试。完整 `npm test` 还需在实现收口后执行。
