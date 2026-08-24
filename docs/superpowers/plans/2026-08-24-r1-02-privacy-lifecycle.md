# R1-02 数据治理生命周期实施计划

**目标：** 在不提前引入 R1-03 多账户系统的前提下，完成当前本地 owner 的成功数据保留、原始载荷完整导出、服务可控数据级联删除和匿名化删除证明。

**边界：** 当前单 owner 会话视为 owner 级账户边界；第三方 provider 已接收的数据不由本地服务伪称可删除，导出只提供本地可控数据和明确的第三方限制。归一化消息和学习结果继续使用现有加密仓储，不把原始正文写入可查询列。

## Task 1: 成功归一化时间与保留策略

**Files:** `src/services/import_service.py`, `src/services/import_repository.py`, `src/services/database.py`, `src/server/config.py`, `src/services/retention_service.py`, `src/server/application.py`, focused unit/integration tests.

- 在不修改既有迁移的前提下追加迁移，保存 `normalized_at` 和成功数据保留截止时间所需的 owner-scoped 元数据。
- 只在解析/归一化成功并完成持久化验证后标记时间；上传完成、预览或失败不能提前触发成功保留清理。
- 新增独立的 normalized retention 配置，默认关闭，最大五年；启动清理只处理已成功归一化且超过截止时间的本地数据，保留导出和删除的稳定计数。
- 保持现有终态 raw retention 行为不变，并补充升级、未成功状态和时区边界测试。

## Task 2: 流式完整导出

**Files:** `src/services/export_service.py`, `src/services/upload_service.py`, `src/server/application.py`, `src/server/http.py`, focused integration tests and docs.

- 新增 owner-scoped archive export，JSON manifest 包含 persona/import/manifest/normalized/learning/conversation/training metadata；原始 payload 以加密对象解密后按固定块流式写入归档，禁止一次性载入内存。
- 导出只允许已认证 owner，使用生成的归档名和安全内部路径；响应声明版本、范围、原始对象数量/字节数以及 provider-side omitted boundary。
- 保留现有 metadata JSON endpoint 作为兼容摘要；新增明确的 archive media type 和失败错误映射，测试跨 owner、空数据、完整字节校验和大对象块大小。

## Task 3: owner 级级联删除与匿名化证明

**Files:** owner deletion service/repository changes, `src/server/application.py`, `src/server/http.py`, database migration/tests.

- 新增显式确认的 `POST /api/v1/data-deletion`，先拒绝进行中的处理任务，再清理 owner 控制的 personas、imports/raw objects、normalized records、profiles、memories、vectors、conversations、consents、training metadata、usage 和 task payloads；本地元数据删除与回执写入在同一事务中完成。
- 删除动作不删除必要的最小匿名化证明：写入独立、不含 owner/token/正文/路径/provider key 的 deletion receipt，仅保存记录版本、时间和有限计数，便于确认结果但不能反向恢复身份。
- 对象存储清理属于事务外的受控边界；任一对象清理或元数据提交失败都返回稳定错误且不生成成功回执，允许用户重试，provider 侧副本和外部训练作业只返回限制项，不伪造已删除。

## Task 4: API、文档与验证

- 更新 README、privacy policy、roadmap 和 Flutter privacy contract，说明 metadata JSON 与 archive export 的差异、保留默认值、删除确认字段、匿名化证明及第三方边界。
- 运行专项单元/集成测试、`compileall`、`git diff --check`、CodeGraph sync 和全量 `npm test`；记录基线中已知 Windows 短路径测试限制，不能把它归因于本分支。

每个任务均按 TDD 顺序执行：先新增失败测试，再实现，专项测试通过后再进入下一任务。用户验收通过后才合并 `main` 并推送远端。
