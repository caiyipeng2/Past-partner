"""Fail-closed master key sources for future authenticated persistence."""

from __future__ import annotations

import base64
import binascii
import ctypes
import os
import secrets
import threading
from pathlib import Path
from typing import Callable, Mapping, Protocol
from uuid import uuid4


MASTER_KEY_BYTES = 32
MASTER_KEY_ENV_VAR = "PAST_PARTNER_MASTER_KEY"
_MAX_PROTECTED_KEY_BYTES = 64 * 1024


class MasterKeyError(RuntimeError):
    """Base error for master key resolution."""


class MasterKeyUnavailableError(MasterKeyError):
    """Raised when a sensitive write has no usable master key."""


class MasterKeyConfigurationError(MasterKeyError):
    """Raised when configured key material cannot be used safely."""


class MasterKeyProvider(Protocol):
    def key_for_sensitive_write(self) -> bytes:
        """Return a 256-bit key or fail before persistence begins."""


class DpapiBackend(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class KmsBackend(Protocol):
    def encrypt(self, key_id: str, plaintext: bytes) -> bytes: ...

    def decrypt(self, key_id: str, ciphertext: bytes) -> bytes: ...


class AwsKmsBackend:
    """Small lazy boto3 adapter for AWS KMS-compatible services."""

    def __init__(
        self,
        *,
        region: str | None = None,
        endpoint: str | None = None,
        client: object | None = None,
    ):
        if client is not None:
            self._client = client
            return
        try:
            import boto3
        except Exception as exc:
            raise MasterKeyConfigurationError("KMS backend is unavailable") from exc
        try:
            self._client = boto3.client(
                "kms",
                region_name=region,
                endpoint_url=endpoint,
            )
        except Exception as exc:
            raise MasterKeyConfigurationError("KMS backend is unavailable") from exc

    def encrypt(self, key_id: str, plaintext: bytes) -> bytes:
        try:
            response = self._client.encrypt(KeyId=key_id, Plaintext=plaintext)
            ciphertext = response.get("CiphertextBlob")
        except Exception as exc:
            raise MasterKeyConfigurationError("KMS encryption failed") from exc
        if not isinstance(ciphertext, bytes) or not ciphertext:
            raise MasterKeyConfigurationError("KMS returned invalid ciphertext")
        return ciphertext

    def decrypt(self, key_id: str, ciphertext: bytes) -> bytes:
        try:
            response = self._client.decrypt(KeyId=key_id, CiphertextBlob=ciphertext)
            plaintext = response.get("Plaintext")
        except Exception as exc:
            raise MasterKeyConfigurationError("KMS decryption failed") from exc
        if not isinstance(plaintext, bytes) or not plaintext:
            raise MasterKeyConfigurationError("KMS returned invalid plaintext")
        return plaintext


class EnvironmentMasterKeyProvider:
    def __init__(self, environ: Mapping[str, str] | None = None):
        # A running process must not silently rotate keys if its environment mapping changes.
        self._environ = dict(os.environ if environ is None else environ)
        self._key: bytes | None = None
        self._lock = threading.Lock()

    def key_for_sensitive_write(self) -> bytes:
        with self._lock:
            if self._key is not None:
                return self._key
            configured = self._environ.get(MASTER_KEY_ENV_VAR)
            if configured is None:
                raise MasterKeyUnavailableError(
                    f"{MASTER_KEY_ENV_VAR} is required before sensitive data can be written"
                )
            if not isinstance(configured, str):
                raise MasterKeyConfigurationError(
                    f"{MASTER_KEY_ENV_VAR} must be a Base64 string"
                )
            if not configured.strip():
                raise MasterKeyUnavailableError(
                    f"{MASTER_KEY_ENV_VAR} is required before sensitive data can be written"
                )
            try:
                decoded = base64.b64decode(configured.strip().encode("ascii"), validate=True)
            except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
                raise MasterKeyConfigurationError(
                    f"{MASTER_KEY_ENV_VAR} must contain strict Base64"
                ) from exc
            self._key = _validated_key(decoded, MASTER_KEY_ENV_VAR)
            return self._key


class UnavailableMasterKeyProvider:
    def key_for_sensitive_write(self) -> bytes:
        raise MasterKeyUnavailableError(
            f"configure {MASTER_KEY_ENV_VAR} before sensitive data can be written"
        )


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("data", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class WindowsDataProtection:
    """Small ctypes wrapper around the current-user Windows DPAPI."""

    _UI_FORBIDDEN = 0x1

    def __init__(self) -> None:
        if os.name != "nt":
            raise MasterKeyConfigurationError("Windows DPAPI is unavailable on this platform")
        self._crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        blob_pointer = ctypes.POINTER(_DataBlob)
        self._crypt32.CryptProtectData.argtypes = [
            blob_pointer,
            ctypes.c_wchar_p,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            blob_pointer,
        ]
        self._crypt32.CryptProtectData.restype = ctypes.c_int
        self._crypt32.CryptUnprotectData.argtypes = [
            blob_pointer,
            ctypes.c_void_p,
            blob_pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            blob_pointer,
        ]
        self._crypt32.CryptUnprotectData.restype = ctypes.c_int
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def protect(self, value: bytes) -> bytes:
        return self._transform(value, protect=True)

    def unprotect(self, value: bytes) -> bytes:
        return self._transform(value, protect=False)

    def _transform(self, value: bytes, protect: bool) -> bytes:
        if not value:
            raise MasterKeyConfigurationError("protected value cannot be empty")
        input_buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        input_blob = _DataBlob(
            len(value), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_ubyte))
        )
        output_blob = _DataBlob()
        if protect:
            succeeded = self._crypt32.CryptProtectData(
                ctypes.byref(input_blob),
                "Past Partner master key",
                None,
                None,
                None,
                self._UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
        else:
            succeeded = self._crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                None,
                None,
                None,
                None,
                self._UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
        if not succeeded:
            raise MasterKeyConfigurationError("Windows DPAPI operation failed")
        try:
            return ctypes.string_at(output_blob.data, output_blob.size)
        finally:
            if output_blob.data:
                self._kernel32.LocalFree(ctypes.cast(output_blob.data, ctypes.c_void_p))


class WindowsDpapiMasterKeyProvider:
    def __init__(
        self,
        protected_key_path: Path | str,
        *,
        backend: DpapiBackend | None = None,
        auto_provision: bool = False,
        random_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ):
        self.protected_key_path = Path(protected_key_path).expanduser().resolve()
        self._backend = WindowsDataProtection() if backend is None else backend
        self._auto_provision = auto_provision
        self._random_bytes = random_bytes
        self._key: bytes | None = None
        self._lock = threading.Lock()

    def key_for_sensitive_write(self) -> bytes:
        with self._lock:
            if self._key is not None:
                return self._key
            if self.protected_key_path.exists():
                if not self.protected_key_path.is_file():
                    raise MasterKeyConfigurationError("DPAPI master key path is not a file")
                self._key = self._load_protected_key()
                return self._key
            if not self._auto_provision:
                raise MasterKeyUnavailableError(
                    "the DPAPI master key has not been provisioned"
                )
            self._key = self._provision_key()
            return self._key

    def _load_protected_key(self) -> bytes:
        try:
            protected = self.protected_key_path.read_bytes()
        except OSError as exc:
            raise MasterKeyConfigurationError("DPAPI master key cannot be read") from exc
        if not protected or len(protected) > _MAX_PROTECTED_KEY_BYTES:
            raise MasterKeyConfigurationError("DPAPI master key file has an invalid size")
        try:
            key = self._backend.unprotect(protected)
        except Exception as exc:
            raise MasterKeyConfigurationError("DPAPI master key cannot be unprotected") from exc
        return _validated_key(key, "DPAPI master key")

    def _provision_key(self) -> bytes:
        key = _validated_key(self._random_bytes(MASTER_KEY_BYTES), "generated master key")
        try:
            protected = self._backend.protect(key)
        except Exception as exc:
            raise MasterKeyConfigurationError("DPAPI master key cannot be protected") from exc
        if not protected or len(protected) > _MAX_PROTECTED_KEY_BYTES:
            raise MasterKeyConfigurationError("DPAPI returned an invalid protected key")

        self.protected_key_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.protected_key_path.with_name(
            f".{self.protected_key_path.name}.{uuid4().hex}.tmp"
        )
        try:
            with temporary.open("xb") as stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                # Linking a fully flushed file is atomic and never overwrites a
                # key another local process may have provisioned concurrently.
                os.link(temporary, self.protected_key_path)
            except FileExistsError:
                return self._load_protected_key()
        except OSError as exc:
            raise MasterKeyConfigurationError("DPAPI master key cannot be persisted") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return key


class KmsMasterKeyProvider:
    """Resolve a data-encryption key from an atomically persisted KMS ciphertext."""

    def __init__(
        self,
        ciphertext_path: Path | str,
        *,
        key_id: str,
        backend: KmsBackend,
        auto_provision: bool = False,
        random_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ):
        self.ciphertext_path = Path(ciphertext_path).expanduser().resolve()
        self.key_id = _validated_kms_key_id(key_id)
        self._backend = backend
        self._auto_provision = auto_provision
        self._random_bytes = random_bytes
        self._key: bytes | None = None
        self._lock = threading.Lock()

    def key_for_sensitive_write(self) -> bytes:
        with self._lock:
            if self._key is not None:
                return self._key
            if self.ciphertext_path.exists():
                if not self.ciphertext_path.is_file():
                    raise MasterKeyConfigurationError("KMS master key path is not a file")
                self._key = self._load_ciphertext()
                return self._key
            if not self._auto_provision:
                raise MasterKeyUnavailableError("the KMS master key has not been provisioned")
            self._key = self._provision_key()
            return self._key

    def _load_ciphertext(self) -> bytes:
        try:
            ciphertext = self.ciphertext_path.read_bytes()
        except OSError as exc:
            raise MasterKeyConfigurationError("KMS master key cannot be read") from exc
        if not ciphertext or len(ciphertext) > _MAX_PROTECTED_KEY_BYTES:
            raise MasterKeyConfigurationError("KMS master key file has an invalid size")
        try:
            key = self._backend.decrypt(self.key_id, ciphertext)
        except MasterKeyError:
            raise
        except Exception as exc:
            raise MasterKeyConfigurationError("KMS master key cannot be decrypted") from exc
        return _validated_key(key, "KMS master key")

    def _provision_key(self) -> bytes:
        key = _validated_key(self._random_bytes(MASTER_KEY_BYTES), "generated master key")
        try:
            ciphertext = self._backend.encrypt(self.key_id, key)
        except MasterKeyError:
            raise
        except Exception as exc:
            raise MasterKeyConfigurationError("KMS master key cannot be encrypted") from exc
        if not isinstance(ciphertext, bytes) or not ciphertext or len(ciphertext) > _MAX_PROTECTED_KEY_BYTES:
            raise MasterKeyConfigurationError("KMS returned an invalid ciphertext")

        self.ciphertext_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.ciphertext_path.with_name(
            f".{self.ciphertext_path.name}.{uuid4().hex}.tmp"
        )
        try:
            with temporary.open("xb") as stream:
                stream.write(ciphertext)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, self.ciphertext_path)
            except FileExistsError:
                return self._load_ciphertext()
        except OSError as exc:
            raise MasterKeyConfigurationError("KMS master key cannot be persisted") from exc
        finally:
            temporary.unlink(missing_ok=True)
        return key


def build_master_key_provider(
    data_dir: Path | str,
    *,
    mode: str,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    dpapi_backend: DpapiBackend | None = None,
    master_key_source: str = "auto",
    kms_key_id: str | None = None,
    kms_ciphertext_path: Path | str | None = None,
    kms_region: str | None = None,
    kms_endpoint: str | None = None,
    kms_auto_provision: bool = False,
    kms_backend: KmsBackend | None = None,
    random_bytes: Callable[[int], bytes] = secrets.token_bytes,
) -> MasterKeyProvider:
    if mode not in {"development", "test", "production"}:
        raise ValueError("mode must be development, test, or production")
    environment = os.environ if environ is None else environ
    source = master_key_source.strip().casefold() if isinstance(master_key_source, str) else ""
    if source not in {"auto", "environment", "dpapi", "kms"}:
        raise MasterKeyConfigurationError("master key source is unsupported")
    if source == "environment":
        return EnvironmentMasterKeyProvider(environment)

    if source == "kms":
        if kms_key_id is None:
            raise MasterKeyConfigurationError("KMS key ID is required")
        return _build_kms_provider(
            data_dir,
            key_id=kms_key_id,
            ciphertext_path=kms_ciphertext_path,
            region=kms_region,
            endpoint=kms_endpoint,
            auto_provision=kms_auto_provision,
            backend=kms_backend,
            random_bytes=random_bytes,
        )

    if source == "auto" and MASTER_KEY_ENV_VAR in environment:
        return EnvironmentMasterKeyProvider(environment)

    if source == "auto" and kms_key_id is not None:
        return _build_kms_provider(
            data_dir,
            key_id=kms_key_id,
            ciphertext_path=kms_ciphertext_path,
            region=kms_region,
            endpoint=kms_endpoint,
            auto_provision=kms_auto_provision,
            backend=kms_backend,
            random_bytes=random_bytes,
        )

    current_platform = os.name if platform_name is None else platform_name
    if source == "dpapi" and not (mode == "development" and current_platform == "nt"):
        raise MasterKeyConfigurationError("Windows DPAPI is unavailable for this master key source")

    # DPAPI is a current-user local development fallback; production requires
    # an injected key or an explicitly configured KMS provider.
    if source in {"auto", "dpapi"} and mode == "development" and current_platform == "nt":
        key_path = Path(data_dir) / "secrets" / "master-key.dpapi"
        return WindowsDpapiMasterKeyProvider(
            key_path,
            backend=dpapi_backend,
            auto_provision=True,
            random_bytes=random_bytes,
        )
    return UnavailableMasterKeyProvider()


def _build_kms_provider(
    data_dir: Path | str,
    *,
    key_id: str,
    ciphertext_path: Path | str | None,
    region: str | None,
    endpoint: str | None,
    auto_provision: bool,
    backend: KmsBackend | None,
    random_bytes: Callable[[int], bytes],
) -> KmsMasterKeyProvider:
    resolved_backend = backend or AwsKmsBackend(region=region, endpoint=endpoint)
    resolved_path = (
        Path(ciphertext_path)
        if ciphertext_path is not None
        else Path(data_dir) / "secrets" / "master-key.kms"
    )
    return KmsMasterKeyProvider(
        resolved_path,
        key_id=key_id,
        backend=resolved_backend,
        auto_provision=auto_provision,
        random_bytes=random_bytes,
    )


def _validated_key(value: object, source: str) -> bytes:
    if not isinstance(value, bytes) or len(value) != MASTER_KEY_BYTES:
        raise MasterKeyConfigurationError(
            f"{source} must resolve to exactly {MASTER_KEY_BYTES} bytes"
        )
    return value


def _validated_kms_key_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 2048
        or any(ord(character) < 33 for character in value)
    ):
        raise MasterKeyConfigurationError("KMS key ID is invalid")
    return value.strip()
