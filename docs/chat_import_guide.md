# 聊天记录导入使用指南

## 支持的文件格式

1. **文本文件** (.txt)
   - 每行一条消息
   - 纯文本格式

2. **JSON文件** (.json)
   - 标准JSON格式的聊天记录
   - 支持数组或对象格式

3. **JSONL文件** (.jsonl)
   - 每行一个聊天消息对象
   - 服务端按内容探测，不依赖文件扩展名

4. **微信文本和 HTML 导出** (.txt, .html, .htm)
   - 支持常见的“时间 + 发送者 + 多行正文”文本导出
   - 支持带消息容器、发送者、时间和正文标记的 HTML 导出
   - 支持 UTF-8、UTF-16 和 GB18030 编码，按内容签名探测而不是只看扩展名

5. **通用 CSV 聊天记录** (.csv 或其他扩展名)
   - 首行必须包含发送者、消息内容和时间列，支持 `sender`/`sender_name`、`content`/`message`、`timestamp`/`time` 等常见别名
   - 支持逗号、分号、制表符和竖线分隔符，以及 UTF-8、UTF-16 和 GB18030 编码
   - 服务端按行流式解析，缺少必需列返回 `unsupported_format`，单行字段损坏或值缺失返回 `invalid_record`，不会生成伪成功结果

6. **通用 XML 聊天记录** (.xml 或其他扩展名)
   - 支持 `<message>`、`<record>`、`<item>`、`<entry>`、`<chat>` 和 `<utterance>` 等常见消息节点
   - 支持属性或子节点中的发送者、内容、时间和消息类型字段，并兼容 UTF-8、UTF-16 和 GB18030
   - 服务端使用流式 XML 解析；无消息节点、结构损坏或字段缺失会返回明确错误，DOCTYPE/ENTITY 声明会被拒绝

7. **QQ 文本和 HTML 导出** (.txt, .html, .htm)
   - 支持常见的“时间 + 发送者 + 多行正文”文本导出
   - 支持带消息容器、发送者、时间和正文标记的 HTML 导出
   - 支持 UTF-8、UTF-16 和 GB18030 编码，解析结果的 `source_type` 为 `qq_text` 或 `qq_html`

8. **微信数据库目录** (`db_storage/`、`Msg/`)
   - 支持微信 3.x/4.x 明文 SQLite 数据库目录，解析前会复制 `.db` 及现有 `-wal`/`-shm` 文件形成一致只读快照
   - 需要上传包含多个数据库文件的目录；单个 `.db` 文件不会被误判为聊天记录
   - 微信 4.x 解析需要完整 `chat_id`，不能用昵称猜测会话；加密数据库和未知 schema 会返回明确错误，不会自动提取密钥

9. **其他常见资料**
   - QQ/聊天原生导出文件、ZIP、HTML
   - PDF、DOCX、常见图片、音频和视频文件
   - P0-18/P0-20/P0-21 已提供 TXT、JSON、JSONL 的统一消息标准化核心；P0-25 增加微信 TXT/HTML 导出解析；P0-26 增加 QQ TXT/HTML 导出解析；P0-27 增加默认关闭的终态原始导入保留期清理；P0-28 提供 owner 级人物、导入任务、参与者映射、预览修正和加密清单元数据导出；P0-29 提供 owner 级人物删除级联清理，并在存在处理中导入时拒绝删除以避免部分清理；P0-30 增加微信明文 SQLite 数据库目录的安全快照和 3.x/4.x 消息标准化；P0-31 增加 QQ 通用消息表明文 SQLite 数据库目录安全快照和消息标准化；P0-32 增加带 manifest v1 的微信 ZIP 备份包安全解析；P0-33 增加带 manifest v1 的 QQ ZIP 备份包安全解析；P1-01 增加通用 CSV 聊天记录解析；P1-02 增加通用 XML 聊天记录解析；微信或 QQ 私有加密备份、媒体、文档、图片、音频和视频解析按后续处理器逐步接入

## 导入方式

### 单文件导入
1. 先创建人物并选择父亲、母亲、亲人、朋友、情侣或自定义关系
2. 点击"选择文件"按钮
3. 选择要导入的聊天记录文件
4. 点击"开始导入"按钮

### 文件夹导入
1. 先保存人物身份
2. 点击"选择文件夹"按钮
3. 选择包含聊天记录文件的文件夹
4. 点击"开始导入"按钮

说明：当前浏览器分片上传会为文件夹中的每个文件建立独立导入任务；P0-30/P0-31 的微信和 QQ 数据库目录解析入口用于服务端/本地预处理目录调用，浏览器端将多个数据库文件自动聚合为一个数据库导入任务仍属于后续任务。

## 微信聊天记录导出方法

### Android设备
1. 使用第三方工具如"微信聊天记录导出助手"
2. 导出为数据库文件(.db)格式
3. 将导出的文件夹上传到本系统

### iOS设备
1. 使用iTunes或爱思助手备份设备
2. 使用第三方工具提取微信聊天记录
3. 导出为数据库文件格式

## 注意事项

1. **文件大小限制**: 单次导入任务默认不超过3 GiB（3,221,225,472 字节）。服务端按任务内所有文件 `total_bytes` 之和判断，不是按单个文件分别判断；客户端按4 MiB分片上传并支持重复分片幂等重试。部署者可通过 `PAST_PARTNER_MAX_IMPORT_BYTES` 调整任务总量上限。
2. **隐私保护**: 原始媒体发送给第三方模型前必须取得按供应商和用途划分的明确授权
3. **数据安全**: 当前本地开发版写入配置的数据目录，并通过回环地址初始化本地 owner Bearer 会话；单个导入和人物级导入数据可通过 API 删除，人物、导入任务和清单元数据可通过 API 导出。设置 `PAST_PARTNER_RAW_RETENTION_SECONDS` 后，服务启动会清理当前 owner 名下过期的 `failed` 或 `cancelled` 导入及其加密对象，默认关闭；成功标准化后的原始数据保留、OIDC 多用户账户和账户级删除仍属于后续生产阶段，不能视为已经完成
4. **格式要求**: 确保文件格式正确，否则可能导致解析失败

## 断点续传状态查询

客户端重新连接后可请求 `GET /api/v1/imports/{import_id}/missing-chunks?expected_chunks=N`。
其中 `N` 是客户端根据自身分片策略计算出的完整分片数量；响应会返回 `received_chunks`、`missing_chunks`、`received_bytes` 和任务状态。省略 `expected_chunks` 时，服务端只报告当前已观测索引范围内的缺口，不推测末尾尚未出现的分片。

## 解析预览

上传完成后可请求 `GET /api/v1/imports/{import_id}/preview?limit=20`。单文件响应包括内容探测得到的 `source_type`、解析摘要和最多 20 条规范化消息；多文件会按清单顺序分别切分和解析，响应额外返回 `file_summaries`，每条记录带有 `file_id`、`source_name`、`media_type` 和文件级 `source_type`。`limit` 是整个导入任务的总记录上限，不超过 100。未完成的导入返回 `preview_unavailable`，无法识别的内容返回 `unsupported_format`，不会伪装成空解析结果。

## 参与者映射

导入完成后，可以把聊天文件中的来源参与者 ID 映射到人物、当前用户或其他参与者。提交接口为 `POST /api/v1/imports/{import_id}/participant-mapping`，请求体示例：

```json
{
  "mapping": {
    "wxid_example": "persona",
    "我": "user",
    "群成员A": "other",
    "未知来源": "unknown"
  }
}
```

角色只支持 `persona`、`user`、`other` 和 `unknown`。服务端会校验来源 ID 的长度和可打印性，并将映射写入现有的加密导入清单；使用 `GET` 同一路径可以回读映射。未完成的导入不能提交映射。
角色或来源 ID 不符合约束时返回 `422 invalid_participant_mapping`；未完成的导入返回 `409 mapping_unavailable`。

## 预览修正

预览中的每条记录都有稳定的 `record_id`，初始审核状态为 `needs_review`。可以提交字段修正和审核状态：

```json
{
  "corrections": [
    {
      "record_id": "预览返回的64位记录ID",
      "fields": {"content": "修正后的消息内容"},
      "review_state": "accepted"
    }
  ]
}
```

接口为 `POST /api/v1/imports/{import_id}/corrections`，支持修正单文件或多文件预览中的 `sender_id`、`sender_name`、`content`、`timestamp` 和 `message_type`。状态只支持 `accepted`、`needs_review` 和 `rejected`；服务端会重新执行消息结构校验，修正结果写入加密导入清单，并在后续预览中应用。

## 支持的数据库文件

- 微信 3.x 数据库目录（`MicroMsg.db` 与 `MSG*.db`）
- 微信 4.x 数据库目录（`contact/contact.db`、`session/session.db`、`message/message_*.db`）
- 现有 `-wal` 和 `-shm` sidecar 会随数据库一起快照，不要求调用方手工拼接

## 常见问题

### Q: 为什么我的数据库文件无法解析？
A: 可能的原因包括：
- 数据库文件损坏
- 微信版本不兼容
- 文件权限问题
- 加密的数据库文件

### Q: 上传的文件会被保存在哪里？
A: 当前本地开发版保存在启动参数指定的数据目录中，文件名和目录由服务端生成。上传分片、合并对象、人物内容字段以及导入任务/上传清单已使用认证加密 SQLite 或对象存储；随机人物和导入 ID 仍是明文非秘密索引，不能据此承诺静态数据已完整加密。

### Q: 我可以删除已上传的文件吗？
A: 可以使用 `DELETE /api/v1/imports/{import_id}` 删除单个导入，或使用 `DELETE /api/v1/personas/{persona_id}` 删除人物及其 owner 名下的全部导入。接口会清理对应的加密分片、合并对象、加密清单和人物元数据；也可以通过 `PAST_PARTNER_RAW_RETENTION_SECONDS` 启用启动时的 `failed`/`cancelled` 终态清理。成功标准化后的保留期、账户级删除和审计仍属于后续功能。

### Q: 我可以导出自己的数据吗？
A: 可以使用 `GET /api/v1/data-export` 导出当前 owner 的人物、导入任务、参与者映射、预览修正和加密清单元数据。为避免一次性缓冲大文件，原始导入载荷、第三方供应商数据和审计记录不包含在当前 JSON 导出中。
