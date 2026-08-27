# P1-03 通用 HTML 聊天记录解析实现记录

## 目标

按原始 P1 顺序新增通用 HTML 聊天记录解析，复用 `ParserRegistry`、`NormalizedMessage` 和导入预览链路。平台标记明确的微信/QQ HTML 继续由专用解析器处理，通用解析器仅覆盖跨平台或无扩展名的常见 HTML 消息导出。

## 范围

- 识别 `message`、`chat-item`、`chat-entry`、`record`、`utterance` 等常见消息容器。
- 支持 `data-*` 属性和发送者、时间、正文子节点的常见别名。
- 支持 HTML 实体、`<br>` 换行、UTF-8、UTF-16 和 GB18030 编码。
- 以流式 `HTMLParser` 处理内容，忽略 `script`、`style` 和 `template` 节点。
- 无消息容器或无有效记录时返回稳定 `unsupported_format`，不伪造空解析成功。
- 保持微信/QQ 专用 HTML 解析器的探测优先级。

## 验证

- 通用 HTML 语义字段、实体和换行测试。
- UTF-16、无 HTML 扩展名和最大预览条数测试。
- 脚本/样式隔离和无消息容器拒绝测试。
- 导入预览 API 集成测试。
- 解析器注册表及全量 Python/Node 回归测试。

## 非目标

不在本任务内实现厂商私有 HTML 模板的专用适配、附件/媒体深度解析、浏览器目录聚合或 HTML 中嵌入的脚本执行。
