import unittest
from pathlib import Path

from src.providers import configuration
from src.providers.catalog import ProviderCatalog


_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "deepseek": "DeepSeek",
    "xiaomi_mimo": "小米 MiMo",
    "qwen": "阿里通义千问",
    "ollama": "Ollama",
    "custom_openai": "自定义 OpenAI-compatible",
    "custom_http": "自定义 HTTP",
}


class PrivacyPolicyContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = (Path.cwd() / "docs" / "privacy_policy.md").read_text(encoding="utf-8")

    def test_policy_does_not_claim_unimplemented_protection(self) -> None:
        unimplemented_claims = (
            "所有敏感数据在存储和传输过程中均进行加密处理",
            "严格的权限管理和身份验证机制",
            "在数据分析和模型训练中使用匿名化技术",
            "不会将用户数据出售、出租或共享给第三方",
            "使用系统内置的数据删除功能",
        )

        for claim in unimplemented_claims:
            with self.subTest(claim=claim):
                self.assertNotIn(claim, self.policy)

    def test_policy_discloses_current_data_flow_and_limits(self) -> None:
        required_disclosures = (
            "本地开发预览",
            "当前版本尚未实现静态数据加密",
            "当前代码已提供 P0-05 AES-256-GCM 认证加密服务",
            "P0-07 已将人物名称、关系和创建时间等内容字段保存为加密 SQLite 字段",
            "P0-08 已将导入任务和上传清单保存为同一事务中的加密 SQLite 字段",
            "当前版本提供单一本地 owner 的 Bearer 会话、人物/导入/上传 owner 归属和未授权拦截",
            "当前版本尚未提供自动保留期清理、数据导出或账户级级联删除功能；已提供按 owner 校验的单个导入删除和人物级导入级联删除接口",
            "文本消息会发送给用户选择且由服务端配置的模型供应商",
            "原始图片、音频或视频不得发送给第三方模型供应商",
            "按供应商、用途、范围和预计费用进行逐次告知并取得授权",
            "API Key 仅从服务端环境变量读取",
            "不得用于骚扰、跟踪、冒充或侵犯他人隐私",
            "https://github.com/caiyipeng2/Past-partner",
        )

        for disclosure in required_disclosures:
            with self.subTest(disclosure=disclosure):
                self.assertIn(disclosure, self.policy)

    def test_policy_separates_runtime_adapters_from_catalog_placeholders(self) -> None:
        runtime_disclosure = next(
            line for line in self.policy.splitlines() if line.startswith("当前运行时适配器已覆盖")
        )
        catalog_only_disclosure = next(
            line for line in self.policy.splitlines() if "目前仅存在于供应商目录中" in line
        )
        catalog_ids = {provider.id for provider in ProviderCatalog.default().providers()}
        runtime_ids = {definition.provider_id for definition in configuration._PROVIDERS}

        self.assertEqual(catalog_ids, set(_PROVIDER_LABELS))
        self.assertLessEqual(runtime_ids, catalog_ids)
        for provider_id, label in _PROVIDER_LABELS.items():
            with self.subTest(provider_id=provider_id):
                if provider_id in runtime_ids:
                    self.assertIn(label, runtime_disclosure)
                    self.assertNotIn(label, catalog_only_disclosure)
                else:
                    self.assertIn(label, catalog_only_disclosure)
                    self.assertNotIn(label, runtime_disclosure)

    def test_policy_preserves_wechat_database_secret_boundaries(self) -> None:
        required_boundaries = (
            "微信数据库直接解析和模型学习尚未在当前项目中实现",
            "数据库、WAL 和 SHM 文件应先形成一致的只读快照",
            "用户明确授权的本地步骤",
            "密钥只允许短暂存在于进程内存中",
            "不得写入日志、配置或持久化文件",
        )

        for boundary in required_boundaries:
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, self.policy)


if __name__ == "__main__":
    unittest.main()
