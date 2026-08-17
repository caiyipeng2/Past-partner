# P4-02 关系型元数据持久化边界设计

**Roadmap position:** Phase 5 / second production-platform slice

## 1. Goal

在不迁移现有数据、不改变 HTTP 或 Flutter 契约的前提下，把人物、导入、授权、训练任务、会话和认证元数据使用的关系型数据库连接与迁移行为收口到一个可替换边界。P4-02 默认继续使用当前认证加密 SQLite，并为后续 PostgreSQL 适配提供明确的连接、事务和迁移契约。

## 2. Current Boundary

当前各个 SQLite repository 接收数据库路径，并分别创建 `sqlite3.Connection`、调用 `SQLiteMigrator` 和拼接 SQLite 方言 SQL。`Application.from_config` 也直接用同一个路径初始化多个 repository。这样可以满足单进程本地运行，但后续 PostgreSQL 适配会同时触及所有 repository、启动装配和错误处理，容易出现部分服务切换或事务语义不一致。

P4-01 已经把上传对象字节与本地路径隔离；P4-02 只处理关系型元数据，不重复包装 `BlobStore`、AES-GCM 或解析临时文件。

## 3. Proposed Architecture

### 3.1 MetadataStore port

新增 `MetadataStore` 协议以及稳定的存储错误类型，业务 repository 只依赖该协议提供的连接/事务入口和 `migrate()`，不再自行决定数据库文件路径或启动迁移。连接协议必须明确：

- 读连接和写事务的生命周期由 store 管理；
- 写事务失败必须回滚，连接必须关闭；
- 每个应用实例的所有 repository 使用同一个 store；
- store 错误不回显 DSN、绝对路径、SQL 参数或加密字段内容；
- dialect/placeholder 等差异由 adapter 处理，repository 不分支判断 SQLite 或 PostgreSQL。

具体 DB-API 类型只保留在 adapter 模块内。不会让 HTTP、domain 或 Flutter 代码看到 `sqlite3.Connection`。

### 3.2 SQLite adapter

`SQLiteMetadataStore` 是 P4-02 的唯一运行时实现：

- 复用现有 `SQLiteMigrator`、数据库文件位置和 migration ledger；
- 默认启用外键、保留当前 `BEGIN IMMEDIATE` 写事务语义；
- 继续保存认证加密字段和 owner/persona 隔离；
- 兼容现有 repository 构造方式的短期 factory 只负责创建 store，不保留 repository 内的隐式全局连接。

P4-02 不改变现有 schema、migration version、加密 AAD 或本地对象布局。

### 3.3 Runtime configuration and wiring

新增 `PAST_PARTNER_METADATA_BACKEND`，默认值为 `sqlite`。启动时：

- `sqlite` 创建 `SQLiteMetadataStore`；
- `postgresql`、`postgres`、空白值和未知值在配置校验阶段明确失败；
- 不因未知后端静默回退 SQLite；
- 本任务不读取 PostgreSQL 凭据，也不添加 `psycopg`/SQLAlchemy 依赖。

`Application.from_config` 只创建一个 store，并把它传给所有 metadata repository；BlobStore 仍按 P4-01 独立装配。

### 3.4 Future PostgreSQL seam

后续 P4-03 才实现 PostgreSQL adapter、DSN/连接池、PostgreSQL migration ledger 和真实服务验证。P4-02 的 port 必须让该 adapter 能表达同样的提交、回滚、关闭和稳定错误语义，但不伪造 PostgreSQL 已可用。

## 4. Compatibility and Safety

- 旧的 `Repository(database_path, encryption)` 调用在 P4-02 过渡期继续有效，由兼容 factory 创建 SQLite store；新代码优先注入 `MetadataStore`。
- owner 条件、加密 payload、persona deletion cascade 和训练/会话状态机保持原行为。
- migration checksum/history 仍 fail-closed；store 初始化失败不得启动一个只部分可用的 Application。
- 任何错误日志禁止包含数据库路径、未来 DSN、SQL 参数、token 或明文消息。

## 5. Non-goals

- 不接入真实 PostgreSQL、Docker、云数据库、连接池或 schema 双写；
- 不迁移现有 SQLite 文件或重写 migration 版本；
- 不改变 HTTP、Web、Flutter、BlobStore、AES-GCM 或 provider API；
- 不增加多租户、审计、计费、队列和监控功能；这些属于后续 Phase 5 任务。

## 6. Verification Boundary

- `MetadataStore` 合同测试覆盖连接生命周期、事务提交/回滚、迁移幂等和稳定错误；
- SQLite adapter 测试确认现有 schema、checksum、外键和加密 repository 回归不变；
- wiring 测试确认所有 repository 共享同一个 store，未知 backend 在写入前失败；
- repository 全量测试继续覆盖 owner 隔离、级联删除、训练/会话状态和 legacy migration；
- 全量 Python、Web、Dart、Flutter 与 `git diff --check` 继续执行；不把不存在的 PostgreSQL 服务当作通过证据。

## 7. Rollout

先在 `codex/p4-02-metadata-boundary` 独立分支提交设计和实施计划，等待用户确认后再实现。验收通过后才合并 `main`、运行合并后回归、推送 `origin/main` 并清理 worktree/分支。
