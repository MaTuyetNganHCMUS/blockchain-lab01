"""Validator identity and fixed public-key registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .ed25519 import verify


@dataclass(frozen=True)
class ValidatorIdentity:
    """Fixed identity binding a validator ID to an Ed25519 public key."""

    validator_id: str
    public_key: bytes

    def __post_init__(self) -> None:
        if not self.validator_id:
            raise ValueError("validator ID cannot be empty")
        if len(self.public_key) != 32:
            raise ValueError("Ed25519 public keys must be 32 bytes")
        object.__setattr__(self, "public_key", bytes(self.public_key))


class ValidatorKeyRegistry:
    """Immutable mapping between validator IDs and Ed25519 public keys."""

    def __init__(
        self,
        keys: Mapping[str, bytes] | tuple[ValidatorIdentity, ...],
    ) -> None:
        if not keys:
            raise ValueError("validator key registry cannot be empty")
        normalized: dict[str, bytes] = {}
        entries = (
            ((identity.validator_id, identity.public_key) for identity in keys)
            if not isinstance(keys, Mapping)
            else keys.items()
        )
        for validator_id, public_key in entries:
            if validator_id in normalized:
                raise ValueError(f"duplicate validator ID: {validator_id}")
            identity = ValidatorIdentity(validator_id, public_key)
            normalized[identity.validator_id] = identity.public_key
        self._keys = MappingProxyType(normalized)

    @property
    def validator_ids(self) -> tuple[str, ...]:
        return tuple(self._keys)

    def get_public_key(self, validator_id: str) -> bytes:
        try:
            return self._keys[validator_id]
        except KeyError as exc:
            raise KeyError(f"unknown validator: {validator_id}") from exc

    def contains(self, validator_id: str) -> bool:
        return validator_id in self._keys

    def verify(
        self,
        validator_id: str,
        domain: str,
        payload: bytes,
        signature: bytes,
    ) -> bool:
        if not self.contains(validator_id):
            return False
        return verify(self.get_public_key(validator_id), domain, payload, signature)


__all__ = ["ValidatorIdentity", "ValidatorKeyRegistry"]
