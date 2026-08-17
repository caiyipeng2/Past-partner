# P4-03 PostgreSQL 元数据适配器设计

**Roadmap position:** Phase 5 / third production-platform slice

## 1. Goal

在 P4-02 `MetadataStore` 边界之上加入可真实运行的 PostgreSQL 元数据适配器，使人物、导入、授权、训练、会话和认证仓储可以在 PostgreSQL 上使用同一套业务语义。默认仍为 SQLite；只有显式配置 DSN 和 `postgresql` 后端时才连接 PostgreSQL。

## 2. Why This Requires More Than A Driver

当前仓储仍保留 SQLite 语法和异常假设：`?` 参数占位符、`BEGIN IMMEDIATE`、`BLOB` 迁移类型、`sqlite3.Binary` 以及 `sqlite3.IntegrityError`。只增加 `psycopg` 并切换连接会导致启动成功但首次写入失败，或把唯一约束/外键错误泄漏为 500。因此 P4-03 同时收口：

- PostgreSQL 连接池和连接生命周期；
- qmark 到 PostgreSQL 参数的适配，以及 `BEGIN IMMEDIATE` 到 PostgreSQL 事务语义的明确映射；
- PostgreSQL `BYTEA` 迁移 DDL 和独立 schema ledger；
- 唯一约束、外键、连接和迁移错误的稳定 `MetadataStoreError` 映射；
- Application 启动和关闭时的后端生命周期。

## 3. Architecture

### 3.1 PostgreSQL adapter

新增 `PostgreSQLMetadataStore`，使用 `psycopg` 3 和 `psycopg_pool.ConnectionPool`：

- DSN 只从 `PAST_PARTNER_METADATA_DSN` 读取，绝不写入日志、错误消息或响应；
- 默认池大小为 `min_size=1`、`max_size=4`，由受限整数配置覆盖；
- `connect()` 从池中借出一个 connection proxy，`close()` 归还池，不关闭共享池；
- `transaction(immediate=True)` 使用 PostgreSQL `BEGIN`，不伪造 SQLite 的锁语义；现有乐观 revision 和事务边界继续保证状态转换原子性；
- `close()` 关闭池，Application 生命周期结束时调用；
- 驱动缺失、DSN 无效、连接池耗尽、迁移失败都转换为不含 DSN/SQL/参数的稳定错误。

### 3.2 Driver-neutral connection contract

扩展 `MetadataConnection`/`MetadataStore` 契约：

- 增加 `close()`，明确应用拥有 store 生命周期；
- `execute(sql, parameters)` 仍是 repository 唯一入口，repository 不判断 SQLite/PostgreSQL；
- PostgreSQL proxy 将安全的 qmark 参数转换为 `%s`，把 `sqlite3.Binary` 产生的 memoryview 归一为 bytes；
- `BEGIN IMMEDIATE` 只在 PostgreSQL proxy 内映射为 `BEGIN`，禁止业务层出现后端分支；
- 唯一约束/外键错误映射为 `MetadataIntegrityError`，连接/事务错误映射为 `MetadataStoreError`；消息只保留稳定 code，不携带驱动原文。

SQLite adapter 也实现同样的 `close()` 和错误边界，默认行为、现有测试和兼容路径不变。

### 3.3 PostgreSQL migrations

新增 PostgreSQL migration runner，复用现有逻辑 migration 版本、名称和 checksum，但编译 PostgreSQL DDL：

- `BLOB` 编译为 `BYTEA`；
- 外键、索引、检查约束和 1-9 版本语义保持一致；
- ledger 使用 PostgreSQL `schema_migrations`，`applied_at` 为 `TIMESTAMPTZ`；
- migration 仍是原子、幂等、checksum fail-closed；
- 不复用 SQLite 文件，也不自动把已有 SQLite 数据复制到 PostgreSQL。

### 3.4 Configuration and wiring

新增配置：

- `PAST_PARTNER_METADATA_BACKEND`: `sqlite`（默认）或 `postgresql`；接受 `postgres` 作为规范化别名；
- `PAST_PARTNER_METADATA_DSN`: PostgreSQL 后端必填；SQLite 后端不读取；
- `PAST_PARTNER_METADATA_POOL_MIN_SIZE`、`PAST_PARTNER_METADATA_POOL_MAX_SIZE`：正整数，限制在合理上限内且 min 不得大于 max。

未知后端、空 DSN、池参数非法或 PostgreSQL 依赖缺失必须在启动阶段明确失败，不得回退 SQLite。`Application.from_config` 创建一个共享 store；服务关闭时释放池。

## 4. Data And Security Boundaries

- 现有 AES-GCM payload、AAD、owner/persona 隔离、BlobStore、本地文件布局和 HTTP/Flutter 契约不变；
- PostgreSQL 只保存已经加密的 metadata envelope 和非秘密索引；
- DSN、用户名、密码、主机、SQL 参数、加密字段和驱动 traceback 不进入日志或 `MetadataStoreError` 文本；
- 不自动迁移现有 SQLite 元数据，部署者必须先完成备份和单独的数据迁移方案；
- 不新增多租户、审计、计费、队列、KMS 或分布式 worker。

## 5. Verification Boundary

- 端口合同测试：生命周期、事务提交/回滚、错误脱敏和关闭幂等；
- PostgreSQL adapter 单测：qmark/memoryview/BEGIN 映射、连接池归还、错误码；
- PostgreSQL migration 测试：空库升级到版本 9、重复迁移、checksum/事务回滚；
- 真实服务集成测试：通过 `PAST_PARTNER_METADATA_DSN` 连接受控 PostgreSQL，覆盖应用启动、人物/导入/授权/训练/会话读写、owner 隔离和级联删除；
- SQLite 回归、Web、Dart、Flutter 和 CodeGraph 继续执行；
- 没有真实 DSN 时只能报告 adapter/contract 通过，不能宣称 PostgreSQL 运行验收通过。

## 6. Non-goals

- 不提供 SQLite 到 PostgreSQL 的历史数据迁移工具；
- 不引入 Docker 编排作为运行时依赖；
- 不接入 S3/KMS、分布式任务、审计、计费或多用户账户；
- 不改变默认 SQLite 开发路径。

## 7. Rollout

先在 `codex/p4-03-postgresql` 完成设计和计划，等待用户确认后实现。实现验收通过后才合并 `main`、运行合并后回归、推送 `origin/main` 并清理 worktree/分支。
