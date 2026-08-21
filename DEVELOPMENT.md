# Past-partner 开发与跨电脑接续手册

本文档是从 Git 克隆后继续开发的单一入口。当前代码基线以 `main` 最新提交为准；本文档编写时的基线是 `f7d9962`（跨电脑开发手册和路线图已合并）。开始工作前先执行 `git pull --ff-only origin main`。

## 1. 项目边界

Past-partner 是一个本地优先的个性化风格伴侣项目，包含 Python API 服务、Web 工作区和 Android-first Flutter 客户端。导入、解析、加密存储、人物画像、长期记忆候选、模型目录、授权、训练任务和会话均以服务端 API 为边界；Flutter 不解析微信/QQ 原始数据库，也不保存供应商密钥。

原始产品设计基线位于 `docs/superpowers/specs/2026-07-30-personalized-companion-platform-design.md`。当前实现状态和后续顺序见 `docs/ROADMAP.md`。不要新增 `P0-34` 之类的临时编号；新增工作使用路线图中的 `R0/R1/R2` 编号，避免偏离原始大纲。

## 2. 推荐环境

以下是可复现开发的最低建议，Windows 10/11 是当前主要开发平台。

| 组件 | 建议版本 | 用途 |
| --- | --- | --- |
| Git | 2.38+ | 分支、worktree、远端同步 |
| Python | 3.11+；当前基线使用 3.14.3 | API 服务、解析器、测试 |
| Node.js | 20+；当前基线使用 26.4.0 | Web 工作区测试和 npm 包装命令 |
| Flutter/Dart | Flutter stable，Dart 满足 `mobile/pubspec.yaml` 的 `>=3.5.0 <4.0.0` | Android 客户端和移动端测试 |
| JDK | 17 | Android Gradle/Flutter 构建 |
| Android SDK + adb | 与已安装 Flutter stable 匹配 | APK、模拟器和真机验收 |
| ffprobe | 8.x 或兼容版本 | 音频/视频媒体元数据检查 |

macOS/Xcode 只在需要 iOS archive、签名或商店发布时安装。Windows 当前只做 iOS 代码、版本和 ATS 静态检查，不宣称可以完成 iOS 真机打包。

确认工具是否可用：

```powershell
git --version
python --version
node --version
flutter --version
java -version
adb version
ffprobe -version
```

## 3. 克隆和安装

```powershell
git clone https://github.com/caiyipeng2/Past-partner.git
Set-Location Past-partner
git switch main
git pull --ff-only origin main

python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-core.txt

# 需要微信/QQ/文档/媒体解析或完整单元测试时安装
python -m pip install -r requirements-parsers.txt
python -m pip install -r requirements-dev.txt

Copy-Item .env.example .env
```

`requirements.txt` 是核心依赖入口；`requirements-parsers.txt` 会额外安装 Pillow、pypdf、numpy 等解析依赖；`requirements-models.txt` 才会安装模型/训练相关的大型依赖；`requirements-storage.txt` 只在使用 S3-compatible 存储时安装。不要为了启动本地服务安装 torch、transformers 或 datasets。

`.env` 只用于本机配置，不能提交。至少应设置一个 32 字节 Base64 的 `PAST_PARTNER_MASTER_KEY`，或仅在 Windows 开发机使用受限的 DPAPI 模式。真实 API Key、PostgreSQL DSN、S3/KMS 凭据、设备配对令牌、Android keystore 和密码都必须留在服务端/构建进程环境中。

## 4. 启动服务

Python 模块、可安装的 `companion-server` CLI、Docker Compose、npm 和 PowerShell
都调用同一套服务入口；npm/PowerShell 仍只是 PC 调试包装，不是唯一运行方式：

```powershell
python -m src.server
python -m pip install -e .
companion-server
npm start
.\scripts\run_server.ps1
docker compose up --build
```

默认监听 `127.0.0.1:8080`，Compose 默认把容器映射到同一端口。服务启动后可访问
`http://127.0.0.1:8080/api/v1/health`。安装 CLI 前先安装 `requirements-core.txt`，
或直接执行 `python -m pip install -e .` 让构建后端安装核心依赖。Compose 使用命名数据卷，
停止并清理本次开发资源时执行 `docker compose down --volumes --remove-orphans`。

统一检查四种入口的真实 health/API 链路（CLI 需要先安装，Compose 会构建并在结束时清理）：

```powershell
python scripts/launch_smoke.py --surface module --surface cli --surface npm --surface compose
```

没有 Docker Desktop 时，前三种入口仍可独立 smoke；不要把 Compose 未执行写成容器运行证据。

服务端可选后端只在显式配置时启用：

- `PAST_PARTNER_METADATA_BACKEND=postgresql`：需要 `PAST_PARTNER_METADATA_DSN` 和可丢弃测试数据库。
- `PAST_PARTNER_STORAGE_BACKEND=s3` 或 `minio`：需要 HTTPS endpoint、bucket、成对凭据和 `requirements-storage.txt`。
- `PAST_PARTNER_MASTER_KEY_SOURCE=kms`：需要 AWS-compatible KMS、key ID 和可丢弃测试密钥。
- 任务队列、审计、用量和指标默认使用当前进程/共享元数据后端，不会自动启动外部 worker、broker 或监控系统。

## 5. 测试和静态检查

在仓库根目录执行 Python/Node 检查：

```powershell
npm test
python -m unittest discover -s tests -p "test*.py" -v
python -m compileall -q src tests
git diff --check
```

外部服务集成测试默认安全跳过，只有显式设置可丢弃环境才会运行。示例：

```powershell
$env:PAST_PARTNER_METADATA_DSN = "postgresql://..."
$env:PAST_PARTNER_METADATA_TEST_DISPOSABLE = "1"
python -m unittest tests.integration.test_postgresql_metadata_store -v

$env:PAST_PARTNER_S3_TEST_ENDPOINT = "https://..."
$env:PAST_PARTNER_S3_TEST_BUCKET = "past-partner-test"
$env:PAST_PARTNER_S3_TEST_ACCESS_KEY = "..."
$env:PAST_PARTNER_S3_TEST_SECRET_KEY = "..."
$env:PAST_PARTNER_S3_TEST_DISPOSABLE = "1"
python -m unittest tests.integration.test_s3_blob_store -v
```

KMS 和真实任务队列测试也必须使用专用测试密钥/数据库，并设置各自的 `*_TEST_ENABLED` 或 disposable 开关。测试结束后删除临时 bucket、数据库、密钥和环境变量；绝不使用生产数据。

R0-01 使用统一 runner 执行四组真实 disposable 回归。runner 会拒绝未显式确认的资源，逐组报告成功/失败，并对输出做脱敏处理；不输出 DSN、密钥或完整连接 URL。PostgreSQL schema、S3 测试对象、KMS 密文文件和临时目录由 fixture 清理；KMS key 必须是预先创建的专用测试 key，生命周期由创建它的环境负责。

```powershell
$env:PAST_PARTNER_DISPOSABLE_RUN = "1"
$env:PAST_PARTNER_METADATA_DSN = "postgresql://<user>:<password>@127.0.0.1:5432/past_partner_test"
$env:PAST_PARTNER_METADATA_TEST_DISPOSABLE = "1"
$env:PAST_PARTNER_S3_TEST_ENDPOINT = "http://127.0.0.1:9000"
$env:PAST_PARTNER_S3_TEST_BUCKET = "past-partner-test"
$env:PAST_PARTNER_S3_TEST_ACCESS_KEY = "<disposable-access-key>"
$env:PAST_PARTNER_S3_TEST_SECRET_KEY = "<disposable-secret-key>"
$env:PAST_PARTNER_S3_TEST_DISPOSABLE = "1"
$env:PAST_PARTNER_KMS_TEST_ENDPOINT = "http://127.0.0.1:4566"
$env:PAST_PARTNER_KMS_TEST_KEY_ID = "alias/past-partner-disposable"
$env:PAST_PARTNER_KMS_TEST_ACCESS_KEY = "<disposable-access-key>"
$env:PAST_PARTNER_KMS_TEST_SECRET_KEY = "<disposable-secret-key>"
$env:PAST_PARTNER_KMS_TEST_DISPOSABLE = "1"
.\scripts\run_disposable_integrations.ps1 -ReportPath .test-runtime\r0-01-report.json
```

该入口不会自动启动或停止外部服务，也不会清理调用方的 KMS key；建议使用本机专用 PostgreSQL 数据库、MinIO bucket 和 LocalStack KMS key。没有这些资源时保持默认跳过，不得把普通 `npm test` 的跳过结果写成真实集成证据。

CodeGraph 用于结构化检查。Windows 下需要在提升权限的终端执行：

```powershell
codegraph sync
codegraph status
```

若工作树没有 `.codegraph` 索引，先执行 `codegraph init -i`。提交前应看到 `Index is up to date`。

## 6. Flutter/Android

```powershell
Set-Location mobile
flutter pub get
flutter analyze
flutter test
flutter devices
adb devices
```

普通本地验收 APK（先回到仓库根目录）：

```powershell
Set-Location ..
.\scripts\build_mobile_apk.ps1 -OutputDirectory E:\Tools
```

商店签名 APK 必须显式使用独立 keystore：

```powershell
$env:PAST_PARTNER_ANDROID_KEYSTORE_FILE = "E:\Secrets\past-partner-upload.jks"
$env:PAST_PARTNER_ANDROID_KEYSTORE_PASSWORD = "<仅当前进程可见>"
$env:PAST_PARTNER_ANDROID_KEY_ALIAS = "past-partner"
$env:PAST_PARTNER_ANDROID_KEY_PASSWORD = "<仅当前进程可见>"
.\scripts\build_mobile_apk.ps1 -StoreRelease -OutputDirectory E:\Tools
```

脚本会清理输出目录中旧的 `Past-partner_*.apk`，产物命名为 `Past-partner_<版本>_<yyyyMMdd_HHmm>_<debug|release>.apk`。APK、JKS 和密码不提交 Git。真机直连需要开发模式的四项 `PAST_PARTNER_DEV_DEVICE_*` TLS 配置；模拟器或 `adb reverse/port-forward` 走回环 HTTP 时不要发送设备配对 header。

如果 Flutter/Gradle 在 Windows 非 ASCII 路径下出现工具链兼容问题，可临时使用 ASCII 目录映射，例如将仓库映射到 `W:` 后再运行 Flutter。映射只是本机临时手段，不改变 Git 路径；`mobile/build/`、`mobile/.dart_tool/` 和生成 APK 已在忽略规则中，不应手工提交。

## 7. 代码结构

```text
src/server/          HTTP 路由、配置、启动方式和鉴权边界
src/services/        加密元数据、导入、任务、授权、会话、审计、用量
src/providers/       模型目录、价格、Provider 适配器和能力门控
src/preprocessing/   微信/QQ/HTML/DOCX/PDF/SQLite/媒体解析
src/learning/        风格画像、长期记忆和本地检索
mobile/              Flutter Android-first 客户端
web/                 Web 工作区和前端测试
scripts/             服务启动、APK 构建和移动端测试脚本
tests/               Python 单元/集成测试与 Node/Flutter 测试
docs/                原始设计、隐私说明和路线图
```

## 8. 分支、验收和合并规则

每个功能使用独立分支和 worktree，示例：

```powershell
git worktree add -b feature/<short-name> .worktrees/<short-name> main
Set-Location .worktrees/<short-name>
```

实现顺序固定为：先读原始设计和路线图 -> 先写失败测试 -> 最小实现 -> 聚焦测试 -> 全量回归 -> 由用户验收。用户验收通过后才允许合并 `main`、运行合并后的全量测试并推送 `origin/main`。未验收分支不得覆盖主分支，不能用“代码已写”代替真实页面、APK 或集成链路证据。

提交前至少检查：

```powershell
git status --short --branch
git diff --check
npm test
git log -1 --oneline
```

保留其他开发者或用户创建的未跟踪文件；不要使用 `git reset --hard`、`git checkout --` 或宽范围删除来“清理”工作区。

## 9. 常见问题

- **导入媒体检测失败**：确认已安装 `requirements-parsers.txt` 和 `ffprobe`；服务不会把缺少工具伪装成检测成功。
- **PostgreSQL/S3/KMS 测试跳过**：这是默认安全行为；配置专用 disposable 资源和对应开关后再运行，不要改成连接生产资源。
- **Flutter 命令找不到或构建慢**：安装 Flutter stable、接受 Android SDK licenses，并检查 `flutter doctor -v`；首次构建会下载 Gradle 依赖。
- **Windows 测试出现 `Path.relative_to` 或临时目录错误**：把 `TEMP` 和 `TMP` 临时指向当前工作树下的 `.test-runtime\temp`，再运行测试；测试结束删除该目录。不要让临时目录跨盘符或混用 8.3 短路径。

```powershell
$env:TEMP = Join-Path (Get-Location) ".test-runtime\temp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
npm test
```
- **真机无法访问服务**：检查 TLS 证书 IP SAN、私有网段 allowlist、手机和电脑是否在同一受控网络；不要把服务绑定到公网或使用 `0.0.0.0/0`。
- **端口被占用**：使用 `python -m src.server --port <port>` 或脚本参数，确保 Web、Flutter 配置和测试使用同一个端口。
- **数据目录不确定**：先停止服务，再核对 `--data-dir`；不要直接删除仓库根目录的 `data/`，除非确认它只包含本次本地运行数据且已备份必要内容。

下一项具体工作以 `docs/ROADMAP.md` 的最高优先级未完成条目为准。
