"""Run one opt-in, redacted text request against a configured provider.

The command is intentionally separate from the normal server startup path. It
requires an explicit environment flag, keeps credentials in the process only,
and prints a bounded summary rather than the prompt or provider response.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.providers.base import ChatMessage, ChatRequest
from src.providers.catalog import ProviderCatalog
from src.providers.configuration import build_provider_adapters
from src.providers.gateway import ProviderError, ProviderGateway


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one redacted real-provider text smoke")
    parser.add_argument(
        "--provider",
        default=os.getenv("PAST_PARTNER_PROVIDER_SMOKE_PROVIDER", "deepseek"),
        help="configured provider id (default: deepseek)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("PAST_PARTNER_PROVIDER_SMOKE_MODEL"),
        help="provider model id; defaults to the first configured model",
    )
    parser.add_argument(
        "--prompt",
        default=os.getenv("PAST_PARTNER_PROVIDER_SMOKE_PROMPT", "请用一句话回复：连接测试成功"),
        help="prompt sent to the provider; never printed by this command",
    )
    args = parser.parse_args(argv)

    if os.getenv("PAST_PARTNER_PROVIDER_SMOKE", "").strip().lower() not in {"1", "true", "yes"}:
        _print_result("disabled", provider=args.provider)
        return 2

    base_catalog = ProviderCatalog.default()
    try:
        adapters = build_provider_adapters(base_catalog, os.environ)
    except (ValueError, TypeError):
        _print_result("invalid_configuration", provider=args.provider)
        return 2

    adapter = adapters.get(args.provider)
    if adapter is None:
        _print_result("provider_not_configured", provider=args.provider)
        return 2

    allowed_models = getattr(getattr(adapter, "config", None), "allowed_models", frozenset())
    model_id = args.model or next(iter(sorted(allowed_models)), None)
    if not model_id:
        _print_result("unknown_model", provider=args.provider)
        return 2

    runtime_models = {
        provider_id: provider_adapter.config.allowed_models
        for provider_id, provider_adapter in adapters.items()
        if hasattr(provider_adapter, "config")
    }
    catalog = base_catalog.with_configured(set(adapters), runtime_models)
    gateway = ProviderGateway(catalog, mode="development", adapters=adapters)
    request = ChatRequest(
        provider_id=args.provider,
        model_id=model_id,
        messages=(ChatMessage(role="user", content=args.prompt),),
    )
    try:
        response = gateway.chat(request)
    except ProviderError as exc:
        _print_result(exc.code, provider=args.provider, model=model_id)
        return 2
    except Exception:
        _print_result("provider_smoke_failed", provider=args.provider, model=model_id)
        return 1

    _print_result(
        "ok",
        provider=args.provider,
        model=model_id,
        response_chars=len(response.content),
        provider_request_id=response.provider_request_id,
        usage=response.usage,
    )
    return 0


def _print_result(status: str, **details: object) -> None:
    payload = {"status": status, **details}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
