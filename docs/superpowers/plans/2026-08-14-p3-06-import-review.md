# P3-06 移动端导入预览与审核

## 目标

在导入任务完成上传后，为 Android 移动端提供可重试的预览、参与者身份映射和逐条记录审核入口。页面只展示服务端已经归一化的有限预览，不在客户端解析微信/QQ 原始文件。

## 范围

- 读取 `GET /api/v1/imports/{id}/preview`，展示摘要、警告和记录。
- 读取并保存 `GET/POST /api/v1/imports/{id}/participant-mapping`，身份仅允许 persona/user/other/unknown。
- 保存 `POST /api/v1/imports/{id}/corrections`，每条修正携带稳定 record id 和 accepted/needs_review/rejected 状态。
- 导入工作区中 uploaded/completed 任务进入审核页；上传中任务继续上传。
- 响应异常转为稳定的中文重试状态，不回显原始响应体。

## 验收标准

1. 预览响应缺少必填字段、record id 非 64 位十六进制或列表超出客户端边界时，客户端拒绝并显示稳定错误。
2. 审核页展示来源、记录/警告数量、截断提示、警告列表、参与者映射和记录审核状态。
3. 修改身份或记录状态后，提交的 JSON 只包含允许字段，失败可重试且不会丢失本地编辑。
4. 上传工作区的已上传任务可通过明确的“查看审核”入口进入页面，原有上传/恢复流程不回归。
5. Flutter 单元/Widget 测试通过；ASCII 路径下静态检查和 Android debug/release 构建通过后再提交验收。
