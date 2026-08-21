"""Small injectable JSON transport shared by provider adapters."""

from __future__ import annotations

import json
import socket
from collections.abc import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.providers.base import AdapterError, JsonObject


JsonTransport = Callable[[str, dict[str, str], JsonObject, float], Mapping[str, object]]


def urllib_json_transport(
    url: str,
    headers: dict[str, str],
    body: JsonObject,
    timeout_seconds: float,
) -> Mapping[str, object]:
    """Send one bounded JSON request and translate network failures at the adapter boundary."""

    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            raise AdapterError("provider_rate_limited", "provider rate limit was reached") from exc
        if exc.code in {408, 504}:
            raise AdapterError("provider_timeout", "provider request timed out") from exc
        raise AdapterError("provider_http_error", f"provider returned HTTP {exc.code}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise AdapterError("provider_timeout", "provider request timed out") from exc
    except URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise AdapterError("provider_timeout", "provider request timed out") from exc
        raise AdapterError("provider_unavailable", "provider could not be reached") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("invalid_provider_response", "provider returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise AdapterError("invalid_provider_response", "provider response must be a JSON object")
    return payload
