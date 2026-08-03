import subprocess
import sys
import unittest
from pathlib import Path


class DependencyGroupContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path.cwd()

    def _entries(self, relative: str) -> list[str]:
        content = (self.root / relative).read_text(encoding="utf-8")
        return [
            line
            for raw_line in content.splitlines()
            if (line := raw_line.strip()) and not line.startswith("#")
        ]

    def test_default_install_only_selects_the_core_group(self) -> None:
        self.assertEqual(["-r requirements-core.txt"], self._entries("requirements.txt"))
        self.assertEqual(
            [
                "fastapi>=0.78.0",
                "uvicorn>=0.18.0",
                "pydantic>=1.9.0",
                "loguru>=0.6.0",
                "python-dotenv>=0.20.0",
            ],
            self._entries("requirements-core.txt"),
        )

    def test_optional_groups_preserve_parser_model_and_dev_dependencies(self) -> None:
        self.assertEqual(
            [
                "-r requirements-core.txt",
                "numpy>=1.21.0",
                "pandas>=1.3.0",
                "nltk>=3.6",
                "jieba>=0.42.1",
                "scikit-learn>=1.0.0",
                "jsonlines>=3.1.0",
            ],
            self._entries("requirements-parsers.txt"),
        )
        self.assertEqual(
            [
                "-r requirements-parsers.txt",
                "torch>=1.9.0",
                "transformers>=4.21.0",
                "datasets>=2.4.0",
            ],
            self._entries("requirements-models.txt"),
        )
        self.assertEqual(
            ["-r requirements-core.txt", "pytest>=7.1.0"],
            self._entries("requirements-dev.txt"),
        )

    def test_core_service_imports_without_site_packages(self) -> None:
        result = subprocess.run(
            [sys.executable, "-S", "-m", "src.server", "--help"],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("--data-dir", result.stdout)

    def test_readme_explains_each_install_profile(self) -> None:
        readme = (self.root / "README.md").read_text(encoding="utf-8")

        for requirements_file in (
            "requirements.txt",
            "requirements-parsers.txt",
            "requirements-models.txt",
            "requirements-dev.txt",
        ):
            with self.subTest(requirements_file=requirements_file):
                self.assertIn(f"python -m pip install -r {requirements_file}", readme)


if __name__ == "__main__":
    unittest.main()
