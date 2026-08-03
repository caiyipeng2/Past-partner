import unittest
from pathlib import Path


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
            "当前版本尚未提供账户登录、身份验证和访问控制",
            "当前版本尚未提供自动保留期清理、数据导出或级联删除功能",
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
        self.assertIn(
            "当前运行时适配器已覆盖 OpenAI、DeepSeek、小米 MiMo、阿里通义千问、Ollama 和自定义 OpenAI-compatible 服务",
            self.policy,
        )
        self.assertIn(
            "Anthropic、Google Gemini 和自定义 HTTP 目前仅存在于供应商目录中，尚未实现可用的运行时适配器",
            self.policy,
        )

    def test_policy_preserves_wechat_database_secret_boundaries(self) -> None:
        required_boundaries = (
            "微信数据库直接解析和模型学习尚未在当前项目中实现",
            "数据库、WAL 和 SHM 文件应先形成一致的只读快照",
            "密钥只允许短暂存在于进程内存中",
            "不得写入日志、配置或持久化文件",
        )

        for boundary in required_boundaries:
            with self.subTest(boundary=boundary):
                self.assertIn(boundary, self.policy)


if __name__ == "__main__":
    unittest.main()
