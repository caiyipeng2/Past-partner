"""Validated runtime configuration loaded at the process boundary."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import os
from dataclasses import dataclass, replace
from pathlib import Path

from cryptography import x509

from src.services.import_service import DEFAULT_MAX_IMPORT_BYTES
from src.services.upload_service import DEFAULT_CHUNK_BYTES

MAX_RAW_RETENTION_SECONDS = 5 * 365 * 24 * 60 * 60
_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_ULA_NETWORK = ipaddress.ip_network("fc00::/7")


@dataclass(frozen=True, slots=True)
class DevicePairingSettings:
    host: ipaddress.IPv4Address | ipaddress.IPv6Address
    allowed_networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
    token_bytes: bytes
    token_fingerprint: bytes
    tls_cert_file: Path
    tls_key_file: Path


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: Path = Path("data/runtime")
    web_dir: Path = Path("web")
    mode: str = "development"
    owner_bootstrap_token: str | None = None
    cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    )
    max_json_bytes: int = 1024 * 1024
    max_chunk_bytes: int = DEFAULT_CHUNK_BYTES
    max_import_bytes: int = DEFAULT_MAX_IMPORT_BYTES
    raw_retention_seconds: int = 0
    model_pricing_json: str | None = None
    device_bootstrap_token: str | None = None
    device_allowed_networks: tuple[str, ...] = ()
    device_tls_cert_file: Path | None = None
    device_tls_key_file: Path | None = None

    @classmethod
    def from_env(cls) -> "ServerConfig":
        default = cls()
        origins = os.getenv("PAST_PARTNER_CORS_ORIGINS")
        return cls(
            host=os.getenv("PAST_PARTNER_HOST", default.host),
            port=_int_env("PAST_PARTNER_PORT", default.port),
            data_dir=Path(os.getenv("PAST_PARTNER_DATA_DIR", str(default.data_dir))),
            web_dir=Path(os.getenv("PAST_PARTNER_WEB_DIR", str(default.web_dir))),
            mode=os.getenv("PAST_PARTNER_MODE", default.mode),
            owner_bootstrap_token=os.getenv("PAST_PARTNER_OWNER_BOOTSTRAP_TOKEN"),
            cors_origins=tuple(item.strip() for item in origins.split(",") if item.strip()) if origins else default.cors_origins,
            max_json_bytes=_int_env("PAST_PARTNER_MAX_JSON_BYTES", default.max_json_bytes),
            max_chunk_bytes=_int_env("PAST_PARTNER_MAX_CHUNK_BYTES", default.max_chunk_bytes),
            max_import_bytes=_int_env("PAST_PARTNER_MAX_IMPORT_BYTES", default.max_import_bytes),
            raw_retention_seconds=_int_env(
                "PAST_PARTNER_RAW_RETENTION_SECONDS", default.raw_retention_seconds
            ),
            model_pricing_json=os.getenv("PAST_PARTNER_MODEL_PRICING_JSON"),
            device_bootstrap_token=os.getenv("PAST_PARTNER_DEV_DEVICE_BOOTSTRAP_TOKEN"),
            device_allowed_networks=_csv_env("PAST_PARTNER_DEV_DEVICE_ALLOWED_NETWORKS"),
            device_tls_cert_file=_path_env("PAST_PARTNER_DEV_DEVICE_TLS_CERT_FILE"),
            device_tls_key_file=_path_env("PAST_PARTNER_DEV_DEVICE_TLS_KEY_FILE"),
        ).validated()

    def validated(self) -> "ServerConfig":
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if self.mode not in {"development", "test", "production"}:
            raise ValueError("mode must be development, test, or production")
        if min(self.max_json_bytes, self.max_chunk_bytes, self.max_import_bytes) <= 0:
            raise ValueError("request and import limits must be positive")
        if not 0 <= self.raw_retention_seconds <= MAX_RAW_RETENTION_SECONDS:
            raise ValueError("raw retention must be between 0 and five years")
        pairing_fields = (
            self.device_bootstrap_token,
            self.device_allowed_networks,
            self.device_tls_cert_file,
            self.device_tls_key_file,
        )
        if any(value for value in pairing_fields) and not all(pairing_fields):
            raise ValueError("device pairing configuration must include all fields")
        if all(pairing_fields):
            self._build_device_pairing_settings()
        return replace(
            self,
            data_dir=self.data_dir.expanduser().resolve(),
            web_dir=self.web_dir.expanduser().resolve(),
            device_tls_cert_file=(
                self.device_tls_cert_file.expanduser().resolve()
                if self.device_tls_cert_file is not None
                else None
            ),
            device_tls_key_file=(
                self.device_tls_key_file.expanduser().resolve()
                if self.device_tls_key_file is not None
                else None
            ),
        )

    @property
    def device_pairing_enabled(self) -> bool:
        return self.device_bootstrap_token is not None

    @property
    def device_pairing_settings(self) -> DevicePairingSettings | None:
        if not self.device_pairing_enabled:
            return None
        return self._build_device_pairing_settings()

    def _build_device_pairing_settings(self) -> DevicePairingSettings:
        if self.mode != "development":
            raise ValueError("device pairing is available only in development mode")
        host = _parse_private_host(self.host)
        token_text = self.device_bootstrap_token
        cert_file = self.device_tls_cert_file
        key_file = self.device_tls_key_file
        if token_text is None or cert_file is None or key_file is None:
            raise ValueError("device pairing configuration must include all fields")
        token_bytes = _decode_device_token(token_text)
        if self.owner_bootstrap_token is not None and hmac.compare_digest(
            self.owner_bootstrap_token.encode("utf-8"), token_text.encode("utf-8")
        ):
            raise ValueError("device and owner bootstrap tokens must differ")
        networks = tuple(_parse_allowed_network(value, host) for value in self.device_allowed_networks)
        if not networks:
            raise ValueError("device pairing allowed network must not be empty")
        cert_file = cert_file.expanduser().resolve()
        key_file = key_file.expanduser().resolve()
        if not cert_file.is_file() or not key_file.is_file():
            raise ValueError("device pairing TLS files must be readable regular files")
        try:
            certificate = x509.load_pem_x509_certificate(cert_file.read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError("device pairing TLS certificate is invalid") from exc
        try:
            sans = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            has_host_san = host in sans.get_values_for_type(x509.IPAddress)
        except x509.ExtensionNotFound as exc:
            raise ValueError("device pairing TLS certificate must contain the configured host IP SAN") from exc
        if not has_host_san:
            raise ValueError("device pairing TLS certificate must contain the configured host IP SAN")
        return DevicePairingSettings(
            host=host,
            allowed_networks=networks,
            token_bytes=token_bytes,
            token_fingerprint=hashlib.sha256(token_bytes).digest(),
            tls_cert_file=cert_file,
            tls_key_file=key_file,
        )


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name)
    return tuple(item.strip() for item in raw.split(",") if item.strip()) if raw else ()


def _path_env(name: str) -> Path | None:
    raw = os.getenv(name)
    return Path(raw) if raw else None


def _parse_private_host(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if "%" in value:
        raise ValueError("device pairing host must be a private IP literal")
    try:
        host = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("device pairing host must be a private IP literal") from exc
    if isinstance(host, ipaddress.IPv4Address):
        if not any(host in network for network in _RFC1918_NETWORKS):
            raise ValueError("device pairing host must be a private IP address")
    elif host.ipv4_mapped is not None or host not in _ULA_NETWORK:
        raise ValueError("device pairing host must be a private IP address")
    return host


def _parse_allowed_network(
    value: str,
    host: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    if "%" in value:
        raise ValueError("device pairing allowed network is invalid")
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError as exc:
        raise ValueError("device pairing allowed network is invalid") from exc
    if network.version != host.version:
        raise ValueError("device pairing allowed network must match host address family")
    if isinstance(network, ipaddress.IPv4Network):
        allowed = any(network.subnet_of(parent) for parent in _RFC1918_NETWORKS)
        minimum_prefix = 24
    else:
        allowed = network.subnet_of(_ULA_NETWORK)
        minimum_prefix = 64
    if not allowed or network.prefixlen < minimum_prefix:
        raise ValueError("device pairing allowed network is too broad or not private")
    return network


def _decode_device_token(value: str) -> bytes:
    try:
        token = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise ValueError("device pairing token must be strict Base64") from exc
    if len(token) < 32:
        raise ValueError("device pairing token must contain at least 32 bytes")
    return token
