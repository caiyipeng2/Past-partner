import json
import os
import shutil
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path


class LaunchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path.cwd()

    def test_python_module_exposes_cli(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "src.server", "--help"],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--data-dir", result.stdout)

    def test_external_worker_module_exposes_bounded_cli(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "src.worker", "--help"],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--worker-id", result.stdout)
        self.assertIn("--once", result.stdout)

    def test_npm_is_only_a_wrapper_around_python_server(self) -> None:
        package = json.loads((self.root / "package.json").read_text(encoding="utf-8"))

        self.assertEqual("python -m src.server", package["scripts"]["start"])
        self.assertEqual("python -m src.server", package["scripts"]["dev"])
        self.assertIn("unittest discover", package["scripts"]["test"])

    def test_legacy_launchers_delegate_to_unified_server(self) -> None:
        for relative in ("web/server.py", "web/server_advanced.py", "web/server.js", "scripts/run_server.ps1"):
            with self.subTest(relative=relative):
                content = (self.root / relative).read_text(encoding="utf-8")
                self.assertIn("src.server", content)

    def test_installable_cli_metadata_points_at_unified_entrypoint(self) -> None:
        manifest_path = self.root / "pyproject.toml"
        self.assertTrue(manifest_path.is_file())
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual("past-partner", manifest["project"]["name"])
        self.assertEqual(
            "src.server.__main__:main",
            manifest["project"]["scripts"]["companion-server"],
        )
        self.assertEqual(
            "src.worker.__main__:main",
            manifest["project"]["scripts"]["companion-worker"],
        )

    def test_compose_declares_health_checked_unified_service(self) -> None:
        compose = (self.root / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("companion-server:", compose)
        self.assertIn('"python", "-m", "src.server"', compose)
        self.assertIn("/api/v1/health", compose)
        self.assertIn("healthcheck:", compose)
        self.assertIn(":8080", compose)

    def test_compose_requires_an_explicit_environment_master_key(self) -> None:
        compose = (self.root / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("PAST_PARTNER_MASTER_KEY:", compose)
        self.assertIn("PAST_PARTNER_MASTER_KEY_SOURCE:", compose)
        self.assertIn("PAST_PARTNER_MASTER_KEY:?", compose)

    def test_compose_smoke_uses_a_disposable_master_key(self) -> None:
        runner = (self.root / "scripts" / "launch_smoke.py").read_text(encoding="utf-8")
        self.assertIn("secrets.token_bytes(32)", runner)
        self.assertIn('PAST_PARTNER_MASTER_KEY', runner)
        self.assertIn('"-p", project_name', runner)

    def test_compose_yaml_parses_with_a_test_master_key(self) -> None:
        docker = shutil.which("docker")
        if docker is None:
            self.skipTest("Docker is not installed")
        env = os.environ.copy()
        env["PAST_PARTNER_MASTER_KEY"] = "A" * 44
        env["PAST_PARTNER_MASTER_KEY_SOURCE"] = "environment"
        result = subprocess.run(
            [docker, "compose", "-p", "past-partner-contract", "-f", str(self.root / "compose.yaml"), "config", "--quiet"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_compose_yaml_fails_closed_without_a_master_key(self) -> None:
        docker = shutil.which("docker")
        if docker is None:
            self.skipTest("Docker is not installed")
        env = os.environ.copy()
        env.pop("PAST_PARTNER_MASTER_KEY", None)
        result = subprocess.run(
            [docker, "compose", "-p", "past-partner-contract-missing-key", "-f", str(self.root / "compose.yaml"), "config"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("PAST_PARTNER_MASTER_KEY", result.stderr)

    def test_cross_surface_smoke_runner_exposes_all_launch_surfaces(self) -> None:
        runner = self.root / "scripts" / "launch_smoke.py"
        self.assertTrue(runner.is_file())
        content = runner.read_text(encoding="utf-8")
        for surface in ("module", "cli", "npm", "compose"):
            with self.subTest(surface=surface):
                self.assertIn(surface, content)
        self.assertIn("/api/v1/health", content)
        result = subprocess.run(
            [sys.executable, str(runner), "--help"],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("compose", result.stdout)


if __name__ == "__main__":
    unittest.main()
