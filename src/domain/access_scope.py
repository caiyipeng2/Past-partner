"""Canonical owner access scopes for local and future multi-user sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class AccessScopeError(ValueError):
    """Raised when a persisted or requested scope set is not canonical."""

    code = "scope_invalid"


_OWNER_READ = "owner:read"
_OWNER_WRITE = "owner:write"
_ALLOWED = frozenset({_OWNER_READ, _OWNER_WRITE})


@dataclass(frozen=True, slots=True)
class AccessScopes:
    """Immutable, canonical capability set attached to an authenticated principal."""

    values: frozenset[str]

    def __post_init__(self) -> None:
        if not self.values or not self.values <= _ALLOWED:
            raise AccessScopeError("scope set contains an unsupported value")

    @classmethod
    def full(cls) -> "AccessScopes":
        return cls(_ALLOWED)

    @classmethod
    def from_values(cls, values: Iterable[str]) -> "AccessScopes":
        if isinstance(values, (str, bytes, bytearray)):
            raise AccessScopeError("scope values must be an iterable of strings")
        try:
            raw = tuple(values)
        except TypeError as exc:
            raise AccessScopeError("scope values must be an iterable of strings") from exc
        if not raw or any(not isinstance(value, str) or not value for value in raw):
            raise AccessScopeError("scope values must be non-empty strings")
        if len(raw) != len(set(raw)):
            raise AccessScopeError("scope values must not contain duplicates")
        return cls(frozenset(raw))

    @classmethod
    def parse(cls, serialized: str) -> "AccessScopes":
        if not isinstance(serialized, str) or not serialized or any(
            value != value.strip() or not value for value in serialized.split(",")
        ):
            raise AccessScopeError("serialized scopes are not canonical")
        return cls.from_values(serialized.split(","))

    def allows(self, scope: str) -> bool:
        return isinstance(scope, str) and scope in self.values

    def serialize(self) -> str:
        return ",".join(sorted(self.values))
