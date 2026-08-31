"""Deterministic hash-chain primitives for redacted audit storage."""

from __future__ import annotations

import base64
import json
from hashlib import sha256
from typing import Any


GENESIS_HASH = "0" * 64


def event_hash(
    *,
    previous_hash: str,
    event_id: str,
    owner_id: str,
    action: str,
    outcome: str,
    resource_type: str,
    resource_id: str,
    occurred_at: str,
    record_version: int,
    encrypted_payload: bytes | bytearray | memoryview,
) -> str:
    """Hash routing metadata and ciphertext without decoding private payloads."""

    if isinstance(encrypted_payload, memoryview):
        encrypted_payload = encrypted_payload.tobytes()
    if isinstance(encrypted_payload, bytearray):
        encrypted_payload = bytes(encrypted_payload)
    fields: dict[str, Any] = {
        "action": action,
        "encrypted_payload": base64.b64encode(encrypted_payload).decode("ascii"),
        "id": event_id,
        "occurred_at": occurred_at,
        "outcome": outcome,
        "owner_id": owner_id,
        "previous_hash": previous_hash,
        "record_version": record_version,
        "resource_id": resource_id,
        "resource_type": resource_type,
    }
    canonical = json.dumps(fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return sha256(canonical).hexdigest()
