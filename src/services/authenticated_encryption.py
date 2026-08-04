"""Versioned AES-GCM envelopes for bounded records and upload segments."""

from __future__ import annotations

import hashlib
import secrets
import struct
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.services.master_key import MASTER_KEY_BYTES, MasterKeyProvider


ENVELOPE_MAGIC = b"PPAE"
ENVELOPE_VERSION = 1
ALGORITHM_AES_256_GCM = 1
DATA_KEY_BYTES = 32
MASTER_KEY_ID_BYTES = 16
NONCE_BYTES = 12
GCM_TAG_BYTES = 16
WRAPPED_DATA_KEY_BYTES = DATA_KEY_BYTES + GCM_TAG_BYTES
DEFAULT_MAX_PLAINTEXT_BYTES = 64 * 1024**2
_MAX_AAD_BYTES = 64 * 1024
_HEADER = struct.Struct(">4sBB16s12s12s")
ENVELOPE_HEADER_BYTES = _HEADER.size
_MIN_ENVELOPE_BYTES = ENVELOPE_HEADER_BYTES + WRAPPED_DATA_KEY_BYTES + GCM_TAG_BYTES
_KEY_WRAP_DOMAIN = b"PastPartner/key-wrap/v1\x00"
_PAYLOAD_DOMAIN = b"PastPartner/payload/v1\x00"
_MASTER_KEY_ID_DOMAIN = b"PastPartner/master-key-id/v1\x00"


class EncryptionError(RuntimeError):
    """Base error for authenticated encryption operations."""


class EncryptionConfigurationError(EncryptionError):
    """Raised when encryption cannot be performed with safe configuration."""


class InvalidEncryptedPayloadError(EncryptionError):
    """Raised when an encrypted envelope cannot be parsed or is unsupported."""


class AuthenticationError(EncryptionError):
    """Raised when a key, payload, or associated-data check fails."""


class AuthenticatedEncryptionService:
    """Encrypt payloads with one-use data keys wrapped by the configured master key."""

    def __init__(
        self,
        master_keys: MasterKeyProvider,
        *,
        random_bytes: Callable[[int], bytes] = secrets.token_bytes,
        key_resolver: Callable[[bytes], bytes] | None = None,
        max_plaintext_bytes: int = DEFAULT_MAX_PLAINTEXT_BYTES,
    ) -> None:
        if not isinstance(max_plaintext_bytes, int) or max_plaintext_bytes <= 0:
            raise ValueError("max_plaintext_bytes must be a positive integer")
        self._master_keys = master_keys
        self._random_bytes = random_bytes
        self._key_resolver = key_resolver
        self._max_plaintext_bytes = max_plaintext_bytes

    @property
    def max_plaintext_bytes(self) -> int:
        return self._max_plaintext_bytes

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        plaintext = _required_bytes(plaintext, "plaintext")
        if len(plaintext) > self._max_plaintext_bytes:
            raise ValueError("plaintext exceeds the configured encryption segment limit")
        aad = _required_aad(aad)
        master_key = self._master_key()
        data_key = self._random(DATA_KEY_BYTES)
        wrap_nonce = self._random(NONCE_BYTES)
        payload_nonce = self._random(NONCE_BYTES)
        header = _HEADER.pack(
            ENVELOPE_MAGIC,
            ENVELOPE_VERSION,
            ALGORITHM_AES_256_GCM,
            master_key_identifier(master_key),
            wrap_nonce,
            payload_nonce,
        )

        wrapped_key = AESGCM(master_key).encrypt(
            wrap_nonce, data_key, _KEY_WRAP_DOMAIN + header + aad
        )
        ciphertext = AESGCM(data_key).encrypt(
            payload_nonce, plaintext, _PAYLOAD_DOMAIN + header + aad
        )
        return header + wrapped_key + ciphertext

    def decrypt(self, envelope: bytes, aad: bytes) -> bytes:
        envelope = _required_bytes(envelope, "envelope")
        aad = _required_aad(aad)
        if len(envelope) < _MIN_ENVELOPE_BYTES:
            raise InvalidEncryptedPayloadError("encrypted envelope is truncated")
        if len(envelope) - _MIN_ENVELOPE_BYTES > self._max_plaintext_bytes:
            raise InvalidEncryptedPayloadError(
                "encrypted envelope exceeds the configured segment limit"
            )

        magic, version, algorithm, key_id, wrap_nonce, payload_nonce = (
            _HEADER.unpack_from(envelope)
        )
        if magic != ENVELOPE_MAGIC:
            raise InvalidEncryptedPayloadError("encrypted envelope has invalid magic")
        if version != ENVELOPE_VERSION:
            raise InvalidEncryptedPayloadError("encrypted envelope version is unsupported")
        if algorithm != ALGORITHM_AES_256_GCM:
            raise InvalidEncryptedPayloadError("encrypted envelope algorithm is unsupported")

        header = envelope[:ENVELOPE_HEADER_BYTES]
        wrapped_key_end = ENVELOPE_HEADER_BYTES + WRAPPED_DATA_KEY_BYTES
        wrapped_key = envelope[ENVELOPE_HEADER_BYTES:wrapped_key_end]
        ciphertext = envelope[wrapped_key_end:]
        try:
            data_key = AESGCM(self._decryption_key(key_id)).decrypt(
                wrap_nonce, wrapped_key, _KEY_WRAP_DOMAIN + header + aad
            )
            if len(data_key) != DATA_KEY_BYTES:
                raise InvalidTag
            return AESGCM(data_key).decrypt(
                payload_nonce, ciphertext, _PAYLOAD_DOMAIN + header + aad
            )
        except InvalidTag as exc:
            # Deliberately collapse key, AAD, and tampering failures to one result.
            raise AuthenticationError("encrypted payload authentication failed") from exc

    def _master_key(self) -> bytes:
        return _validated_master_key(self._master_keys.key_for_sensitive_write())

    def _decryption_key(self, key_id: bytes) -> bytes:
        if self._key_resolver is None:
            key = self._master_key()
            if not secrets.compare_digest(master_key_identifier(key), key_id):
                raise AuthenticationError("encrypted payload authentication failed")
            return key
        try:
            key = self._key_resolver(key_id)
        except LookupError as exc:
            raise AuthenticationError("encrypted payload authentication failed") from exc
        return _validated_master_key(key)

    def _random(self, length: int) -> bytes:
        try:
            value = self._random_bytes(length)
        except Exception as exc:
            raise EncryptionConfigurationError("random source failed") from exc
        if not isinstance(value, bytes) or len(value) != length:
            raise EncryptionConfigurationError(
                f"random source must return exactly {length} bytes"
            )
        return value


def master_key_identifier(key: bytes) -> bytes:
    """Return a non-secret stable identifier suitable for authenticated headers."""

    key = _validated_master_key(key)
    return hashlib.sha256(_MASTER_KEY_ID_DOMAIN + key).digest()[:MASTER_KEY_ID_BYTES]


def _validated_master_key(key: object) -> bytes:
    if not isinstance(key, bytes) or len(key) != MASTER_KEY_BYTES:
        raise EncryptionConfigurationError(
            f"master key provider must return exactly {MASTER_KEY_BYTES} bytes"
        )
    return key


def _required_bytes(value: object, field_name: str) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError(f"{field_name} must be bytes")
    return value


def _required_aad(value: object) -> bytes:
    aad = _required_bytes(value, "aad")
    if not aad:
        raise TypeError("aad must be non-empty bytes")
    if len(aad) > _MAX_AAD_BYTES:
        raise ValueError(f"aad cannot exceed {_MAX_AAD_BYTES} bytes")
    return aad
