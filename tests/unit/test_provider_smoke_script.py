import contextlib
import importlib.util
import io
import json
from pathlib import Path
import os
import unittest
from unittest.mock import patch

from src.providers.base import ChatResponse


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "provider_smoke.py"
SPEC = importlib.util.spec_from_file_location("provider_smoke_script", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
provider_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider_smoke)


class _FakeAdapter:
    provider_id = "deepseek"

    class _Config:
        allowed_models = frozenset({"deepseek-v4-flash"})

    config = _Config()

    def supports_model(self, model_id: str) -> bool:
        return model_id == "deepseek-v4-flash"

    def chat(self, _request):
        return ChatResponse(
            provider_id="deepseek",
            model_id="deepseek-v4-flash",
            content="secret response text",
            provider_request_id="request-1",
        )


class ProviderSmokeScriptTests(unittest.TestCase):
    def test_smoke_is_disabled_without_explicit_opt_in(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, {"PAST_PARTNER_PROVIDER_SMOKE": ""}, clear=False):
            with contextlib.redirect_stdout(output):
                result = provider_smoke.main(["--provider", "deepseek"])

        self.assertEqual(2, result)
        self.assertEqual("disabled", json.loads(output.getvalue())["status"])

    def test_success_output_is_redacted(self) -> None:
        output = io.StringIO()
        with patch.dict(os.environ, {"PAST_PARTNER_PROVIDER_SMOKE": "1"}, clear=False):
            with patch.object(
                provider_smoke,
                "build_provider_adapters",
                return_value={"deepseek": _FakeAdapter()},
            ):
                with contextlib.redirect_stdout(output):
                    result = provider_smoke.main(
                        ["--provider", "deepseek", "--model", "deepseek-v4-flash", "--prompt", "secret prompt"]
                    )

        self.assertEqual(0, result)
        rendered = output.getvalue()
        self.assertNotIn("secret prompt", rendered)
        self.assertNotIn("secret response text", rendered)
        self.assertEqual("ok", json.loads(rendered)["status"])


if __name__ == "__main__":
    unittest.main()
