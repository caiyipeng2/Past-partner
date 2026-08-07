# P0-32 微信备份包安全解析实现记录

## 目标

对照原始产品大纲，为用户主动选择的微信 ZIP 备份包提供可复用的、受边界保护的解析入口，输出项目统一的 `NormalizedMessage`。本任务定义并支持项目 manifest v1，不宣称兼容微信私有加密备份格式。

## 实现边界

- 新增 `wechat_backup` parser，要求 ZIP 根目录存在 `manifest.json` 或 `wechat-manifest.json`，且 `schema_version=1`、平台标识为微信。
- manifest 可以声明标准化 `records`，或声明 TXT、HTML、JSON、JSONL 消息文件；文件格式必须显式可识别，内部文件复用现有 parser registry。
- ZIP 解析不使用 `extractall`；拒绝路径穿越、反斜杠/绝对路径、符号链接、重复条目、嵌套 ZIP 和未被 manifest 覆盖的文件。
- 施加条目数量、单条目大小、总展开大小、manifest 大小和压缩比限制；解压时再次按实际写入字节数限制，防止伪造 ZIP 头部绕过检查。
- 源 ZIP 只读，不会修改原文件；损坏包、缺少 manifest、平台不匹配和不支持的内部格式返回稳定错误。
- 微信私有加密备份、数据库目录聚合、媒体学习和浏览器端目录聚合不在本任务范围。

## 验收证据

`tests/unit/test_wechat_backup.py` 覆盖 manifest JSONL 解析、预览截断、源文件只读、路径穿越、嵌套 ZIP、条目数量限制、缺失/错误 manifest 和损坏 ZIP。完整 `npm test`、编译检查和 CodeGraph 回归在实现收口后执行。
