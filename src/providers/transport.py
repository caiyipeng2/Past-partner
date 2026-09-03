"""Small injectable JSON transport shared by provider adapters."""

from __future__ import annotations

import json
import http.client
import socket
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from src.providers.base import AdapterError, JsonObject


JsonTransport = Callable[[str, dict[str, str], JsonObject, float], Mapping[str, object]]
JsonRequestTransport = Callable[
    [str, str, dict[str, str], JsonObject | None, float], Mapping[str, object]
]
MultipartTransport = Callable[
    [str, dict[str, str], dict[str, str], str, Path, float], Mapping[str, object]
]
MediaMultipartTransport = Callable[
    [str, dict[str, str], dict[str, str], str, Path, float, str], Mapping[str, object]
]


def urllib_json_transport(
    url: str,
    headers: dict[str, str],
    body: JsonObject,
    timeout_seconds: float,
) -> Mapping[str, object]:
    """Send one bounded JSON request and translate network failures at the adapter boundary."""

    return urllib_json_request_transport("POST", url, headers, body, timeout_seconds)


def urllib_json_request_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: JsonObject | None,
    timeout_seconds: float,
) -> Mapping[str, object]:
    """Send a bounded JSON request for provider APIs that use multiple verbs."""

    normalized_method = method.strip().upper()
    if normalized_method not in {"GET", "POST", "DELETE"}:
        raise AdapterError("invalid_provider_request", "provider request method is not supported")
    request_headers = dict(headers)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = Request(url, data=data, headers=request_headers, method=normalized_method)
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


def urllib_multipart_transport(
    url: str,
    headers: dict[str, str],
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    timeout_seconds: float,
    file_content_type: str | None = None,
) -> Mapping[str, object]:
    """Upload one file without materializing the dataset in memory.

    ``urllib.request`` accepts only an in-memory bytes body for multipart data.
    Fine-tuning datasets can be large, so this boundary writes the multipart
    envelope and file in bounded chunks through ``http.client`` instead.
    """

    if not file_path.is_file():
        raise AdapterError("dataset_unavailable", "training dataset is unavailable")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AdapterError("invalid_provider_request", "provider upload URL is invalid")
    boundary = f"----PastPartner{uuid4().hex}"
    filename = file_path.name or "dataset.jsonl"
    content_type = file_content_type or "application/jsonl"
    if not isinstance(content_type, str) or "/" not in content_type or "\r" in content_type or "\n" in content_type:
        raise AdapterError("invalid_provider_request", "multipart file content type is invalid")
    if file_content_type is not None and file_path.suffix.casefold() == ".bin":
        filename = _media_filename(content_type)
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    parts.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
            ).encode("utf-8"),
        ]
    )
    epilogue = f"\r\n--{boundary}--\r\n".encode("utf-8")
    try:
        connection: http.client.HTTPConnection
        if parsed.scheme == "https":
            connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=timeout_seconds)
        else:
            connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout_seconds)
        request_target = parsed.path or "/"
        if parsed.query:
            request_target += f"?{parsed.query}"
        content_length = sum(len(part) for part in parts) + file_path.stat().st_size + len(epilogue)
        request_headers = dict(headers)
        request_headers.update(
            {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(content_length),
            }
        )
        connection.putrequest("POST", request_target)
        for name, value in request_headers.items():
            connection.putheader(name, value)
        connection.endheaders()
        for part in parts:
            connection.send(part)
        with file_path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                connection.send(chunk)
        connection.send(epilogue)
        response = connection.getresponse()
        raw_payload = response.read(8 * 1024 * 1024 + 1)
        status = response.status
        connection.close()
        if status == 429:
            raise AdapterError("provider_rate_limited", "provider rate limit was reached")
        if status in {408, 504}:
            raise AdapterError("provider_timeout", "provider request timed out")
        if status >= 400:
            raise AdapterError("provider_http_error", f"provider returned HTTP {status}")
        payload = json.loads(raw_payload.decode("utf-8"))
    except AdapterError:
        raise
    except (socket.timeout, TimeoutError) as exc:
        raise AdapterError("provider_timeout", "provider request timed out") from exc
    except (OSError, URLError, http.client.HTTPException) as exc:
        raise AdapterError("provider_unavailable", "provider could not be reached") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("invalid_provider_response", "provider returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise AdapterError("invalid_provider_response", "provider response must be a JSON object")
    return payload


def _media_filename(content_type: str) -> str:
    media_category = content_type.split("/", 1)[0].casefold()
    extension = {
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/mp4": "m4a",
        "audio/x-m4a": "m4a",
        "audio/ogg": "ogg",
        "audio/webm": "webm",
        "audio/flac": "flac",
        "video/mp4": "mp4",
        "video/webm": "webm",
        "video/quicktime": "mov",
        "video/x-msvideo": "avi",
    }.get(content_type.casefold())
    if extension is None:
        subtype = content_type.split("/", 1)[1].split(";", 1)[0].casefold()
        extension = "".join(character for character in subtype if character.isalnum()) or "audio"
    return f"{media_category}.{extension}"
