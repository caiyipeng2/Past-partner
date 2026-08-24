"""Validated runtime configuration loaded at the process boundary."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import ipaddress
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlsplit

from cryptography import x509

from src.services.import_service import DEFAULT_MAX_IMPORT_BYTES
from src.services.upload_service import DEFAULT_CHUNK_BYTES

MAX_RAW_RETENTION_SECONDS = 5 * 365 * 24 * 60 * 60
MAX_NORMALIZED_RETENTION_SECONDS = MAX_RAW_RETENTION_SECONDS
MAX_METADATA_POOL_SIZE = 64
_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_ULA_NETWORK = ipaddress.ip_network("fc00::/7")
_S3_BUCKET = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{1,61}[a-z0-9])?$")


class ConfigurationError(ValueError):
    """Stable startup configuration error without echoing secret input."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


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
    master_key_source: str = "auto"
    master_key_kms_key_id: str | None = None
    master_key_kms_ciphertext_file: Path | None = None
    master_key_kms_region: str = "us-east-1"
    master_key_kms_endpoint: str | None = None
    master_key_kms_auto_provision: bool = False
    storage_backend: str = "local"
    storage_s3_endpoint: str | None = None
    storage_s3_bucket: str | None = None
    storage_s3_region: str = "us-east-1"
    storage_s3_access_key: str | None = None
    storage_s3_secret_key: str | None = None
    storage_s3_session_token: str | None = None
    storage_s3_path_style: bool = True
    metadata_backend: str = "sqlite"
    metadata_dsn: str | None = None
    metadata_pool_min_size: int = 1
    metadata_pool_max_size: int = 4
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
    normalized_retention_seconds: int = 0
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
            master_key_source=os.getenv("PAST_PARTNER_MASTER_KEY_SOURCE", default.master_key_source),
            master_key_kms_key_id=os.getenv("PAST_PARTNER_MASTER_KEY_KMS_KEY_ID"),
            master_key_kms_ciphertext_file=_path_env("PAST_PARTNER_MASTER_KEY_KMS_CIPHERTEXT_FILE"),
            master_key_kms_region=os.getenv(
                "PAST_PARTNER_MASTER_KEY_KMS_REGION", default.master_key_kms_region
            ),
            master_key_kms_endpoint=os.getenv("PAST_PARTNER_MASTER_KEY_KMS_ENDPOINT"),
            master_key_kms_auto_provision=_bool_env(
                "PAST_PARTNER_MASTER_KEY_KMS_AUTO_PROVISION",
                default.master_key_kms_auto_provision,
                error_code="master_key_kms_auto_provision_invalid",
            ),
            storage_backend=os.getenv("PAST_PARTNER_STORAGE_BACKEND", default.storage_backend),
            storage_s3_endpoint=os.getenv("PAST_PARTNER_STORAGE_S3_ENDPOINT"),
            storage_s3_bucket=os.getenv("PAST_PARTNER_STORAGE_S3_BUCKET"),
            storage_s3_region=os.getenv("PAST_PARTNER_STORAGE_S3_REGION", default.storage_s3_region),
            storage_s3_access_key=os.getenv("PAST_PARTNER_STORAGE_S3_ACCESS_KEY"),
            storage_s3_secret_key=os.getenv("PAST_PARTNER_STORAGE_S3_SECRET_KEY"),
            storage_s3_session_token=os.getenv("PAST_PARTNER_STORAGE_S3_SESSION_TOKEN"),
            storage_s3_path_style=_bool_env(
                "PAST_PARTNER_STORAGE_S3_PATH_STYLE", default.storage_s3_path_style
            ),
            metadata_backend=os.getenv("PAST_PARTNER_METADATA_BACKEND", default.metadata_backend),
            metadata_dsn=os.getenv("PAST_PARTNER_METADATA_DSN"),
            metadata_pool_min_size=_int_env(
                "PAST_PARTNER_METADATA_POOL_MIN_SIZE", default.metadata_pool_min_size
            ),
            metadata_pool_max_size=_int_env(
                "PAST_PARTNER_METADATA_POOL_MAX_SIZE", default.metadata_pool_max_size
            ),
            owner_bootstrap_token=os.getenv("PAST_PARTNER_OWNER_BOOTSTRAP_TOKEN"),
            cors_origins=tuple(item.strip() for item in origins.split(",") if item.strip()) if origins else default.cors_origins,
            max_json_bytes=_int_env("PAST_PARTNER_MAX_JSON_BYTES", default.max_json_bytes),
            max_chunk_bytes=_int_env("PAST_PARTNER_MAX_CHUNK_BYTES", default.max_chunk_bytes),
            max_import_bytes=_int_env("PAST_PARTNER_MAX_IMPORT_BYTES", default.max_import_bytes),
            raw_retention_seconds=_int_env(
                "PAST_PARTNER_RAW_RETENTION_SECONDS", default.raw_retention_seconds
            ),
            normalized_retention_seconds=_int_env(
                "PAST_PARTNER_NORMALIZED_RETENTION_SECONDS", default.normalized_retention_seconds
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
        master_key_source = _validate_master_key_settings(self)
        data_dir = self.data_dir.expanduser().resolve()
        kms_ciphertext_file = self.master_key_kms_ciphertext_file
        if kms_ciphertext_file is None and (
            master_key_source == "kms" or self.master_key_kms_key_id is not None
        ):
            kms_ciphertext_file = data_dir / "secrets" / "master-key.kms"
        elif kms_ciphertext_file is not None:
            kms_ciphertext_file = kms_ciphertext_file.expanduser().resolve()
        storage_backend = self.storage_backend.strip().lower() if isinstance(self.storage_backend, str) else ""
        if storage_backend == "minio":
            storage_backend = "s3"
        if storage_backend not in {"local", "s3"}:
            raise ConfigurationError("storage_backend_unsupported", "storage backend is unsupported")
        if storage_backend == "s3":
            _validate_s3_settings(self)
        metadata_backend = self.metadata_backend.strip().lower() if isinstance(self.metadata_backend, str) else ""
        if metadata_backend == "postgres":
            metadata_backend = "postgresql"
        if metadata_backend not in {"sqlite", "postgresql"}:
            raise ConfigurationError(
                "metadata_backend_unsupported",
                "metadata backend is unsupported",
            )
        if not 1 <= self.metadata_pool_min_size <= self.metadata_pool_max_size <= MAX_METADATA_POOL_SIZE:
            raise ConfigurationError("metadata_pool_invalid", "metadata pool size is invalid")
        if metadata_backend == "postgresql" and not isinstance(self.metadata_dsn, str):
            raise ConfigurationError("metadata_dsn_required", "metadata PostgreSQL DSN is required")
        if metadata_backend == "postgresql" and not self.metadata_dsn.strip():
            raise ConfigurationError("metadata_dsn_required", "metadata PostgreSQL DSN is required")
        if min(self.max_json_bytes, self.max_chunk_bytes, self.max_import_bytes) <= 0:
            raise ValueError("request and import limits must be positive")
        if not 0 <= self.raw_retention_seconds <= MAX_RAW_RETENTION_SECONDS:
            raise ValueError("raw retention must be between 0 and five years")
        if not 0 <= self.normalized_retention_seconds <= MAX_NORMALIZED_RETENTION_SECONDS:
            raise ValueError("normalized retention must be between 0 and five years")
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
            master_key_source=master_key_source,
            master_key_kms_ciphertext_file=kms_ciphertext_file,
            storage_backend=storage_backend,
            metadata_backend=metadata_backend,
            data_dir=data_dir,
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
        if self.owner_bootstrap_token is not None:
            owner_bytes = self.owner_bootstrap_token.encode("utf-8")
            same_spelling = hmac.compare_digest(owner_bytes, token_text.encode("utf-8"))
            same_decoded_value = False
            try:
                decoded_owner = base64.b64decode(owner_bytes, validate=True)
                same_decoded_value = len(decoded_owner) == len(token_bytes) and hmac.compare_digest(
                    decoded_owner, token_bytes
                )
            except (binascii.Error, ValueError):
                pass
            if same_spelling or same_decoded_value:
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


def _bool_env(name: str, default: bool, *, error_code: str = "storage_path_style_invalid") -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(error_code, "boolean setting is invalid")


def _validate_master_key_settings(config: ServerConfig) -> str:
    source = config.master_key_source.strip().casefold() if isinstance(config.master_key_source, str) else ""
    if source not in {"auto", "environment", "dpapi", "kms"}:
        raise ConfigurationError("master_key_source_unsupported", "master key source is unsupported")
    if source == "dpapi" and config.mode != "development":
        raise ConfigurationError("master_key_dpapi_unsupported", "DPAPI master keys require development mode")

    key_id = config.master_key_kms_key_id
    kms_values_present = any(
        value is not None
        for value in (
            config.master_key_kms_key_id,
            config.master_key_kms_ciphertext_file,
            config.master_key_kms_endpoint,
        )
    )
    if key_id is not None:
        if (
            not isinstance(key_id, str)
            or not key_id.strip()
            or len(key_id) > 2048
            or any(ord(character) < 33 for character in key_id)
        ):
            raise ConfigurationError("master_key_kms_key_id_invalid", "KMS key ID is invalid")
    if source == "kms" and key_id is None:
        raise ConfigurationError("master_key_kms_key_id_required", "KMS key ID is required")
    if source in {"environment", "dpapi"} and kms_values_present:
        raise ConfigurationError("master_key_source_conflict", "KMS settings conflict with the selected master key source")
    if source == "auto" and kms_values_present and key_id is None:
        raise ConfigurationError("master_key_kms_key_id_required", "KMS key ID is required")

    region = config.master_key_kms_region
    if not isinstance(region, str) or not region.strip() or len(region) > 63 or any(ord(character) < 33 for character in region):
        raise ConfigurationError("master_key_kms_region_invalid", "KMS region is invalid")
    endpoint = config.master_key_kms_endpoint
    if endpoint is not None and endpoint.strip():
        parsed = urlsplit(endpoint.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ConfigurationError("master_key_kms_endpoint_invalid", "KMS endpoint is invalid")
        if parsed.query or parsed.fragment:
            raise ConfigurationError("master_key_kms_endpoint_invalid", "KMS endpoint is invalid")
        if parsed.scheme == "http":
            hostname = (parsed.hostname or "").casefold().rstrip(".")
            if config.mode == "production" or hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ConfigurationError("master_key_kms_endpoint_insecure", "KMS endpoint must use HTTPS")
    return source


def _validate_s3_settings(config: ServerConfig) -> None:
    bucket = config.storage_s3_bucket.strip() if isinstance(config.storage_s3_bucket, str) else ""
    if not bucket or _S3_BUCKET.fullmatch(bucket) is None or len(bucket) > 63:
        raise ConfigurationError("storage_bucket_required", "S3 bucket is required and invalid")
    region = config.storage_s3_region.strip() if isinstance(config.storage_s3_region, str) else ""
    if not region or len(region) > 63 or any(ord(character) < 33 for character in region):
        raise ConfigurationError("storage_region_invalid", "S3 region is invalid")
    access = config.storage_s3_access_key
    secret = config.storage_s3_secret_key
    if bool(access) != bool(secret):
        raise ConfigurationError("storage_credentials_invalid", "S3 credentials must be provided together")
    endpoint = config.storage_s3_endpoint
    if endpoint is None or not endpoint.strip():
        return
    parsed = urlsplit(endpoint.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ConfigurationError("storage_endpoint_invalid", "S3 endpoint is invalid")
    if parsed.query or parsed.fragment:
        raise ConfigurationError("storage_endpoint_invalid", "S3 endpoint is invalid")
    if parsed.scheme == "http":
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        if config.mode == "production" or hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ConfigurationError("storage_endpoint_insecure", "S3 endpoint must use HTTPS")


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
