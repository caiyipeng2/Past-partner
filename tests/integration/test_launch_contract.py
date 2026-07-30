import json
import subprocess
import sys
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


if __name__ == "__main__":
    unittest.main()
