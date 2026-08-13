# P3-03 Flutter 导入任务工作区实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已连接的 Flutter 人物工作区中增加导入任务入口，创建导入元数据并查看任务状态，为后续文件选择、分片上传和审核提供稳定移动端边界。

**Architecture:** 复用现有 `ApiClient` 的认证会话和标准错误封装，新增轻量 `ImportJob` 模型、网关与控制器。人物工作区通过人物卡片进入该人物的导入任务页面；本任务只提交后端已支持的 `POST /api/v1/imports` 元数据，不读取本地文件、不上传字节、不伪造处理进度。

**Tech Stack:** Flutter/Dart、Material 3、`http`、Flutter widget/unit tests。

---

### Task 1: 导入模型和 API 契约

**Files:**
- Create: `mobile/lib/features/imports/import_job.dart`
- Modify: `mobile/lib/core/network/api_client.dart`
- Create: `mobile/lib/features/imports/import_gateway.dart`
- Test: `mobile/test/features/imports/import_gateway_test.dart`

- [x] 写红测试：解析 `GET /api/v1/imports` 返回的任务列表；提交 `POST /api/v1/imports` 时发送 `persona_id/source_name/total_bytes/media_type`；拒绝缺失 id/state 的不完整响应。
- [x] 运行聚焦测试确认契约缺失后失败。
- [x] 实现最小模型、`ApiClient.listImports/createImport` 和网关，所有错误继续映射为稳定 `ApiFailure`。
- [x] 重跑聚焦测试，4/4 通过。

### Task 2: 导入控制器

**Files:**
- Create: `mobile/lib/features/imports/import_controller.dart`
- Test: `mobile/test/features/imports/import_controller_test.dart`

- [x] 写红测试：初始加载、成功创建后刷新、创建失败保留可重试错误；控制器始终携带当前 persona id。
- [x] 运行聚焦测试确认失败。
- [x] 实现加载/创建状态与稳定用户提示，不暴露原始响应正文。
- [x] 重跑聚焦测试，2/2 通过。

### Task 3: 导入任务工作区和人物入口

**Files:**
- Create: `mobile/lib/features/imports/import_workspace_screen.dart`
- Modify: `mobile/lib/features/persona/persona_workspace_screen.dart`
- Modify: `mobile/lib/app/app_dependencies.dart`
- Test: `mobile/test/features/imports/import_workspace_screen_test.dart`
- Test: `mobile/test/features/persona/persona_workspace_screen_test.dart`

- [x] 写红 widget 测试：人物卡片可进入导入任务页；空状态有“创建导入任务”入口；表单校验来源名称、非负字节数、媒体类型；创建后列表显示状态和已接收/总字节数；失败显示重试。
- [x] 运行聚焦 widget 测试确认失败。
- [x] 实现页面和路由入口，明确文案“暂未选择文件，下一步可上传文件”，不提供未实现的文件/相机/媒体按钮。
- [x] 重跑聚焦 widget 测试，4/4 通过；后端 persona 过滤集成测试 1/1 通过。

### Task 4: 回归、构建和交付

**Files:**
- Modify: `docs/superpowers/plans/2026-08-13-p3-03-import-mobile.md`

- [x] 运行 `E:\Tools\flutter\bin\dart.bat analyze`，无问题。
- [x] 运行 `E:\Tools\flutter\bin\flutter.bat test --no-pub`，25/25 通过。
- [x] 从 ASCII 路径构建 Android Debug/Release APK，均成功；Release Manifest 保留 `INTERNET` 与 `usesCleartextTraffic=false`。
- [x] 运行 `git diff --check`，提交 P3-03 独立分支；保持未合并，交付用户验收。

### 验收标准

1. 人物列表中的人物卡片可进入对应导入任务工作区，persona id 不可被页面状态混用。
2. 导入任务元数据创建成功后立即刷新，显示 `created/uploading/uploaded/processing/completed/failed/cancelled` 状态及字节进度。
3. 来源名称、媒体类型和总字节数有必填/非负校验；服务端错误只显示稳定用户提示。
4. 本任务不读取文件、不发送文件字节、不声称后台上传或处理已完成。
5. Dart 分析、Flutter 全量测试、Android Debug/Release 构建通过；iOS 仅验证共享 Dart 源码。
