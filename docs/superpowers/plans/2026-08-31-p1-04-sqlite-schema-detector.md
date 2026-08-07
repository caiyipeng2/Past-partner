# P1-04 通用 SQLite schema 自动探测实现记录

## 目标

按已确认的 P1 后续边界，为用户主动选择的通用 SQLite 数据库目录提供 schema 自动探测和统一消息解析，复用 `ParserRegistry` 与 `NormalizedMessage`。微信/QQ 已识别目录继续由平台解析器优先处理。

## 范围

- 接受包含 `.db` 文件的用户选择目录，不接受单个 `.db` 文件作为聊天导入来源。
- 复制数据库及现有 `-wal`、`-shm` sidecar，比较快照前后文件状态并以只读 SQLite 查询。
- 识别常见消息表名以及 sender、content、timestamp、message_type 等字段别名。
- 输出统一消息、消息类型映射、稳定错误码和 `generic_messages` schema 摘要。
- 对非 SQLite/加密库、未知 schema、空消息和源文件变化失败，不猜测密钥或写入源目录。

## 验证

- 通用目录自动探测、字段别名和消息类型映射。
- `max_records` 预览上限及稳定记录 ID。
- 未知 schema、非 SQLite 文件和单文件边界。
- WAL/SHM 快照、源变化重试和源目录只读验证。
- 解析器及全量 Python/Node 回归测试。

## 非目标

不在本任务内实现微信/QQ 私有加密数据库解密、浏览器目录聚合、跨库会话推断、附件媒体提取或数据库写入。
