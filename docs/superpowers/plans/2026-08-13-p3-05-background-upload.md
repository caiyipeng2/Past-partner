# P3-05 移动端上传恢复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and execute each checkbox with a fresh verification command.

**Goal:** 让已创建的移动端导入任务在应用进程重启后保留安全的本地文件引用，重新进入任务时可以无须重复选择文件而继续上传，并在完成后清理恢复记录。

**Architecture:** 使用 `flutter_secure_storage` 保存按导入任务隔离的最小恢复清单，只保存用户主动选择文件的显示名、媒体类型、大小和受保护的本地引用，不保存访问令牌或文件内容。上传控制器在创建任务后原子写入恢复清单，失败或中断时保留，成功完成后删除；页面点击未完成任务时优先尝试恢复，找不到恢复清单才打开系统文件选择器。

**Tech Stack:** Flutter/Dart、`flutter_secure_storage`、Flutter unit/widget tests、PowerShell APK build helper。

---

### Task 1: 恢复清单模型与安全存储契约

**Files:**
- Create: `mobile/lib/features/imports/import_resume.dart`
- Create: `mobile/test/features/imports/import_resume_test.dart`

- [ ] 写失败测试：恢复文件清单严格解析字段、拒绝空路径/负大小/错误版本，并验证内存存储的写入、读取、删除。
- [ ] 运行 `flutter test test/features/imports/import_resume_test.dart`，确认新类型缺失导致红测。
- [ ] 实现 `ImportUploadResume`、`ImportResumeFile`、`ImportResumeStore`、`InMemoryImportResumeStore` 和 `SecureImportResumeStore`；键名只由导入 ID 组成，JSON 不包含 token 或文件字节。
- [ ] 重跑聚焦测试和 `dart analyze`，确认绿测。

### Task 2: 上传控制器进程恢复生命周期

**Files:**
- Modify: `mobile/lib/features/imports/import_file.dart`
- Modify: `mobile/lib/features/imports/import_upload_controller.dart`
- Modify: `mobile/test/features/imports/import_upload_controller_test.dart`

- [ ] 写失败测试：真实随机访问文件创建任务后写入恢复清单；网络失败保留清单；`resume` 从清单重建文件并跳过服务端已有分片；完成后删除清单；清单缺失时返回稳定错误。
- [ ] 运行聚焦测试确认控制器尚无恢复 API。
- [ ] 让 `RandomAccessImportFile` 暴露受控的本地引用；控制器增加 `resumeStore`、`hasResume` 和 `resume`，仅允许可恢复文件类型写入清单，存储失败时在发起上传前稳定失败。
- [ ] 在成功完成后删除清单；删除失败不得伪造上传失败，保留独立的可重试提示/诊断状态。
- [ ] 重跑上传控制器聚焦测试和全量 Flutter 测试。

### Task 3: 导入工作区接入无重复选择恢复

**Files:**
- Modify: `mobile/lib/features/imports/import_workspace_screen.dart`
- Modify: `mobile/lib/app/past_partner_app.dart`
- Modify: `mobile/test/features/imports/import_workspace_screen_test.dart`

- [ ] 写 widget 测试：未完成任务点击时优先调用恢复；恢复清单不存在时才调用文件选择器；成功完成后任务列表刷新。
- [ ] 将恢复路径接入任务卡片动作，保留原有重新选择文件的回退路径，并显示“继续上传”而不是伪造后台完成状态。
- [ ] 应用层统一注入 `SecureImportResumeStore`，测试继续使用内存替身；不把本地完整路径渲染到页面或日志。
- [ ] 重跑导入工作区测试、全量测试和分析。

### Task 4: APK 产出命名和旧包清理脚本

**Files:**
- Create: `scripts/build_mobile_apk.ps1`
- Create: `scripts/build_mobile_apk_test.ps1`

- [ ] 写 PowerShell 级测试，验证输出名为 `Past-partner_<version>_<yyyyMMdd_HHmm>_<debug|release>.apk`，并且目标目录只保留最新一组 APK。
- [ ] 运行测试确认脚本缺失导致红测。
- [ ] 实现脚本：从 `mobile/pubspec.yaml` 读取版本，分别构建 Debug/Release，复制到 `E:\Tools`，删除旧的 `Past-partner_*.apk` 和当前构建目录中的 APK，不触碰源码或 Git 未跟踪目录。
- [ ] 用 `-WhatIf`/临时工具目录验证删除边界，再实际构建并检查命名产物。

### Task 5: 回归、真机包和交付

**Files:**
- Modify: `docs/superpowers/plans/2026-08-13-p3-05-background-upload.md`
- Modify: `README.md`

- [ ] 运行 `flutter test`, `dart analyze`、相关后端导入回归和 `git diff --check`。
- [ ] 通过命名脚本构建 Android Debug/Release，确认 Release Manifest 仍禁止明文传输，并记录新包绝对路径。
- [ ] 更新文档说明恢复清单只保存安全本地引用，进程重启恢复不等同于操作系统级后台服务。
- [ ] 提交独立分支并保持未合并，交给用户 Android 真机验收；用户验收通过后再合并 `main`、全量回归并推送远端。
