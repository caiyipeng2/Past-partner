"""Run an explicit, redacted Qwen fine-tuning lifecycle smoke.

This command is opt-in because it uploads data and can incur provider charges.
It submits one synthetic example, reads one status response, and cancels an
active job so a developer can validate credentials and endpoint permissions
without sending personal conversation data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.providers.base import FineTuningRequest
from src.providers.catalog import ProviderCatalog
from src.providers.configuration import build_provider_adapters
from src.providers.qwen_fine_tuning import QwenFineTuningAdapter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a redacted Qwen fine-tuning smoke")
    parser.add_argument("--model", default=os.getenv("PAST_PARTNER_QWEN_FINE_TUNING_SMOKE_MODEL"))
    args = parser.parse_args(argv)

    if os.getenv("PAST_PARTNER_QWEN_FINE_TUNING_SMOKE", "").strip().casefold() not in {"1", "true", "yes"}:
        _print_result("disabled")
        return 2
    try:
        adapters = build_provider_adapters(ProviderCatalog.default(), os.environ)
    except (ValueError, TypeError):
        _print_result("invalid_configuration")
        return 2
    adapter = adapters.get("qwen")
    if not isinstance(adapter, QwenFineTuningAdapter):
        _print_result("fine_tuning_not_configured")
        return 2
    model_id = args.model or next(iter(sorted(adapter.config.fine_tuning_models)), None)
    if not model_id or not adapter.supports_fine_tuning(model_id):
        _print_result("capability_not_supported")
        return 2

    with tempfile.TemporaryDirectory(prefix="past-partner-qwen-smoke-") as temp_dir:
        dataset = Path(temp_dir) / "smoke.jsonl"
        dataset.write_text(
            '{"messages":[{"role":"user","content":"smoke"},{"role":"assistant","content":"smoke"}]}\n',
            encoding="utf-8",
        )
        request = FineTuningRequest(
            provider_id="qwen",
            model_id=model_id,
            job_id="past-partner-smoke",
            dataset_path=dataset,
            dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
            sample_count=1,
        )
        try:
            submission = adapter.submit_fine_tuning(request)
            status = adapter.get_fine_tuning_job(submission.provider_job_id)
            if status.state in {"queued", "running"}:
                status = adapter.cancel_fine_tuning_job(submission.provider_job_id)
        except Exception as exc:
            _print_result("failed", error_code=getattr(exc, "code", "provider_smoke_failed"))
            return 1

    _print_result(
        "ok",
        provider="qwen",
        model=model_id,
        state=status.state,
        provider_job_id_present=bool(submission.provider_job_id),
        artifact_present=bool(status.artifact_id),
        evaluation_present=bool(status.evaluation),
    )
    return 0


def _print_result(status: str, **details: object) -> None:
    print(json.dumps({"status": status, **details}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
