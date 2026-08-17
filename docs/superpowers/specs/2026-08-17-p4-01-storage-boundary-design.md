# P4-01 生产存储边界设计

**Status:** Approved design baseline
**Date:** 2026-08-17
**Roadmap position:** Phase 5 / first production-platform slice
**Depends on:** P0-01 through P3-10 merged on `main`

## 1. Goal

为本地 SQLite/文件存储与后续 PostgreSQL/S3-compatible/KMS 生产实现建立最小、可测试、可替换的对象存储边界。P4-01 只收口协议、默认本地适配器、运行时选择和错误契约，不连接真实云资源，也不迁移现有数据。

## 2. Current Boundary

当前上传链路由 `StorageLayout` 直接生成本地路径，`UploadService` 直接读写分片、合并载荷和预览临时对象；人物、导入、授权和训练元数据由 SQLite 仓储保存。AES-GCM 服务已经独立提供认证加密和主密钥解析。

P4-01 的职责是隔离对象字节读写，不把元数据仓储、加密密钥和业务状态事务混合到同一个新抽象中。

## 3. Architecture

### 3.1 Object storage port

新增 `BlobStore` 协议，业务层只使用逻辑对象 key，不接触绝对路径：

```python
class BlobStore(Protocol):
    def put(
        self,
        key: str,
        source: BinaryIO,
        *,
        length: int,
        sha256: str,
    ) -> BlobReceipt: ...

    def iter_bytes(self, key: str, *, block_bytes: int) -> Iterator[bytes]: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> bool: ...
```

`BlobReceipt` 只包含逻辑 key、确认的字节数和 SHA-256，不返回本地路径、凭据或原始正文。

### 3.2 Local adapter

`LocalBlobStore` 是 P4-01 唯一实现。它复用现有 `StorageLayout` 的根目录和目录布局，把路径解析、临时文件、原子替换和删除细节封装在适配器内。

写入流程必须是：

1. 校验 key、声明长度和 SHA-256 格式。
2. 在目标目录创建不可预测的临时文件。
3. 以有界块读取输入，同时计算实际长度和 digest。
4. 实际值不匹配时删除临时文件并返回稳定错误。
5. `flush`、`fsync` 后使用原子替换提交目标对象。
6. 任何异常都清理临时文件，不能留下半成品目标。

读取通过 `iter_bytes` 返回有界块，不能为了适配协议把完整 3 GiB 对象读入内存。删除是幂等的：目标不存在时返回 `False`，不把正常重复删除当成内部异常。

### 3.3 Service integration

`UploadService` 增加可选的 `blob_store` 构造参数：

```python
UploadService(..., blob_store: BlobStore | None = None)
```

未传入时，从现有 `StorageLayout` 自动创建 `LocalBlobStore`，保持旧调用方和测试构造方式有效。分片、合并载荷、预览临时对象和删除路径统一通过该边界；HTTP 路由、SQLite 清单、AES-GCM 信封和 Flutter API 不改变。

`MasterKeyProvider`、`AuthenticatedEncryptionService` 和元数据仓储继续保持现有接口，不被 `BlobStore` 重新包装。

## 4. Logical keys and safety

对象 key 必须是 UTF-8 相对路径，经过规范化后满足：

- 不为空；
- 不以 `/` 或 `\\` 开头；
- 不包含盘符、NUL 或 `..` 路径段；
- 不解析到配置根目录之外；
- 不把 owner token、provider key、绝对路径或原始正文放入 key。

兼容现有本地布局时，适配器将既有 `StorageLayout.object_path` 结果映射为同等逻辑对象；P4-01 不做目录搬迁和历史对象重命名。未来多租户适配器可在不改业务 key 的前提下增加受控命名空间。

## 5. Runtime configuration

新增配置项：

```text
PAST_PARTNER_STORAGE_BACKEND=local
```

规则：

- 缺省值为 `local`，与当前 PC 调试和 Python 直接启动行为一致。
- 当前只注册 `local`；`s3`、`minio`、`postgres` 或其他值在启动配置校验阶段返回 `storage_backend_unsupported`。
- 不允许未实现后端静默回退到本地，避免生产部署误把对象写入本机磁盘。
- 配置错误不回显 token、密钥、完整路径或请求正文。

P4-01 不增加云 SDK、Docker 服务、PostgreSQL 驱动、KMS 客户端或凭据配置。后续适配器必须在独立任务中加入，并继续复用同一协议。

## 6. Error contract

对象适配器抛出稳定的 `StorageError` 子类：

- `invalid_key`
- `object_not_found`
- `object_conflict`
- `storage_read_failed`
- `storage_write_failed`
- `storage_backend_unsupported`

服务层将这些错误映射为现有 `UploadError`，继续通过现有 HTTP 错误包装返回 `error.code` 和 `error.diagnostic_id`。适配器不向客户端暴露 OSError 文本、绝对路径、临时文件名或堆栈。

## 7. Compatibility and non-goals

P4-01 不做以下工作：

- 不迁移 SQLite 元数据仓储到 PostgreSQL；
- 不接入 S3-compatible、MinIO 或 KMS；
- 不改变数据库 schema、导入 API、聊天 API、Flutter API 或 APK 构建链路；
- 不改变现有 AES-GCM 密文格式和主密钥轮换行为；
- 不删除或搬迁既有本地对象。

完成后，默认本地运行的业务结果必须与 P3-10 合并前一致。

## 8. Verification boundary

### 8.1 BlobStore contract tests

合同测试覆盖：

- 合法 key 的写入、读取、存在性和删除；
- `..`、绝对路径、盘符、NUL 和空 key 拒绝；
- 声明长度或 digest 不匹配时不提交目标对象；
- 写入异常后临时文件清理；
- 原子提交前旧对象保持可读；
- `iter_bytes` 按块返回且不要求完整载荷驻留内存；
- 重复删除返回 `False`；
- 适配器错误保持稳定 code 且不泄露本地路径。

### 8.2 Configuration tests

配置测试覆盖默认 `local`、显式 `local`、未知后端拒绝和禁止静默回退；错误消息不得包含 secret 或完整路径。

### 8.3 Upload regression

现有上传服务测试继续验证分片上传、重复分片、校验失败、合并、预览、取消、单个导入删除和人物级级联删除。测试通过注入 `BlobStore` 观察服务确实不再直接依赖绝对路径。

### 8.4 Repository checks

P4-01 完成前执行：

```powershell
python -m unittest discover -s tests -p "test*.py" -v
node --test tests/web_workspace_test.mjs
dart analyze --format=machine
flutter test
git diff --check
```

本地验收不要求真实云服务在线；未实现后端必须通过显式拒绝测试。

## 9. Rollout

先在独立 `codex/p4-01-storage-boundary` 分支实现和测试，保持分支未合并直到用户验收。验收通过后，按既定流程合并 `main`、运行合并后回归、推送 `origin/main`，并清理已合并的临时 worktree 和分支。
