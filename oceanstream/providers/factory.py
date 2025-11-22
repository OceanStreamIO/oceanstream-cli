from __future__ import annotations
from typing import Type

from .base import ProviderBase
from .saildrone import SaildroneProvider
from .r2r.r2r import R2RProvider

_REGISTRY: dict[str, Type[ProviderBase]] = {
    "saildrone": SaildroneProvider,
    "r2r": R2RProvider,
}

def get_provider(name: str) -> ProviderBase:
    key = name.lower().strip()
    if key not in _REGISTRY:
        raise ValueError(f"Unknown provider '{name}'. Available: {sorted(_REGISTRY.keys())}")
    return _REGISTRY[key]()


def list_providers() -> list[str]:
    """Return a sorted list of all registered provider names."""
    return sorted(_REGISTRY.keys())
