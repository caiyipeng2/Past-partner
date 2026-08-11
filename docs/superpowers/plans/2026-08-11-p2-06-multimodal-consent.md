# P2-06 多模态授权能力门控

## 目标

在多模态媒体发送或处理前，把 P1-10 的精确授权记录与当前供应商/模型目录能力组合校验，避免仅凭用户选择了授权记录就把媒体发送给不支持或未声明对应能力的模型。

## 范围

- 新增 `MultimodalConsentGate`，按 `image/photo/picture/vision` -> `vision`、`audio/voice/sound` -> `audio`、`video` -> `video` 映射能力。
- 同时检查 provider、model 存在性，provider/model 能力声明，活动授权状态，以及 provider、model、数据类别和授权范围的精确匹配。
- 新增 `POST /api/v1/consents/{consent_id}/authorize`，返回 `authorized`、所需能力、能力证据和授权 ID，不包含媒体内容。
- 能力不足、未知 provider/model、撤回授权和范围不匹配均返回稳定错误码。

## 明确不包含

- 不自动发送、读取或转换媒体，不替代供应商的隐私政策或实际能力验证。
- 不新增媒体存储、向量、模型训练或多用户授权体系。
