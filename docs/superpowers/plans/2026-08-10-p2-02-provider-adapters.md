# P2-02 Provider 适配器

## 目标

按设计 Phase 3 的“原生云端和本地 Provider adapters”要求，收口统一 Provider 构建入口，并让目录中声明的主要云端模型拥有真实的文本聊天适配器。适配器继续由统一网关调用，客户端不接触凭据。

## 本次范围

- 保留 OpenAI-compatible 适配器，覆盖 OpenAI、DeepSeek、小米 MiMo、阿里千问、Ollama 和自定义 OpenAI-compatible。
- 新增 Anthropic Messages 文本适配器。
- 新增 Google Gemini `generateContent` 文本适配器。
- 抽取共享 JSON HTTP 传输层，支持注入测试传输并统一网络、HTTP 和 JSON 错误。
- 通过服务端环境变量构建适配器，未配置凭据时不启用适配器。
- 更新运行时目录、隐私政策、环境变量模板和适配器契约测试。

## 不在本次范围

- 不实现流式响应、Embedding、媒体分析、微调、模型自动发现或实时价格同步。
- 不实现自定义 HTTP 插件 SDK、用户 BYOK 凭据仓或多用户密钥隔离。
- 不在测试中访问真实供应商网络，不伪造模型回复或供应商可用状态。

## 验收标准

1. 统一构建入口可以从环境变量装配 OpenAI-compatible、Anthropic 和 Gemini 适配器，API Key 不进入模型目录或请求响应。
2. Anthropic 请求使用 Messages `/v1/messages` 结构，Gemini 请求使用 `generateContent` 结构，均能将响应规范化为统一 `ChatResponse`。
3. 不支持的模型、空消息和无文本响应返回稳定适配器错误；HTTP 层将供应商网络/响应错误映射为 502。
4. 未配置供应商仍返回 `provider_not_configured`，测试 Provider 仍只允许在 test 模式启用。
5. 定向适配器测试、完整回归、编译检查和 CodeGraph 同步通过。
