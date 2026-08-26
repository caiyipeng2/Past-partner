"""Static contract for the opt-in disposable integration runner.

The runner is intentionally a PowerShell wrapper because the supported
cross-machine workflow is Windows-first.  These tests keep the safety
properties reviewable without requiring Docker or cloud credentials on every
developer workstation.
"""

from __future__ import annotations

from pathlib import Path
import unittest


class DisposableIntegrationRunnerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path.cwd()
        cls.runner = cls.root / "scripts" / "run_disposable_integrations.ps1"

    def test_runner_exists_and_is_explicitly_opt_in(self) -> None:
        self.assertTrue(self.runner.is_file())
        content = self.runner.read_text(encoding="utf-8")
        for marker in (
            "PAST_PARTNER_DISPOSABLE_RUN",
            "PAST_PARTNER_MASTER_KEY",
            "PAST_PARTNER_METADATA_DSN",
            "PAST_PARTNER_METADATA_TEST_DISPOSABLE",
            "PAST_PARTNER_S3_TEST_DISPOSABLE",
            "PAST_PARTNER_KMS_TEST_DISPOSABLE",
            "finally",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, content)

    def test_runner_executes_all_external_contracts_and_redacts_output(self) -> None:
        content = self.runner.read_text(encoding="utf-8")
        for module in (
            "tests.integration.test_postgresql_metadata_store",
            "tests.integration.test_s3_blob_store",
            "tests.integration.test_kms_master_key",
            "tests.integration.test_task_queue_backends",
        ):
            with self.subTest(module=module):
                self.assertIn(module, content)
        for marker in (
            "postgres(?:ql)?://",
            "PAST_PARTNER_S3_TEST_SECRET_KEY",
            "ConvertTo-SafeOutput",
            "RandomNumberGenerator",
            "ToBase64String",
            "Remove-Item Env:PAST_PARTNER_MASTER_KEY",
            "skipped=\\d+",
            "failure_code",
            "failed_module",
            "configuration_rejected",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, content)

    def test_runner_report_is_machine_readable_and_records_each_failure_class(self) -> None:
        content = self.runner.read_text(encoding="utf-8")
        for marker in (
            "module = $Module",
            "status = $status",
            "exit_code = $exitCode",
            "failure_code = $failureCode",
            "skipped_tests",
            "module_failed",
            "runner_process_failed",
            "failed_module = $failedModule",
            "results = $results",
            "failure_code = $failureCode",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, content)

    def test_development_docs_describe_the_single_runner_and_no_secret_logging(self) -> None:
        docs = (self.root / "DEVELOPMENT.md").read_text(encoding="utf-8")
        roadmap = (self.root / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
        self.assertIn("run_disposable_integrations.ps1", docs)
        self.assertIn("PAST_PARTNER_DISPOSABLE_RUN", docs)
        self.assertIn("不输出 DSN、密钥或完整连接 URL", docs)
        self.assertIn("R0-01", roadmap)
        self.assertIn("统一 runner", roadmap)


if __name__ == "__main__":
    unittest.main()
