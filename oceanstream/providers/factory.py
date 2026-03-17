from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .base import ProviderBase
from .cmems import CmemsProvider
from .emodnet import EmodnetProvider
from .emso import EmsoProvider
from .generic import GenericProvider
from .norsoop import NorsoopProvider
from .oceanlab import OceanlabProvider
from .ooi import OoiProvider
from .plocan import PlocanProvider
from .r2r.r2r import R2RProvider
from .saildrone import NoaaPmelProvider, SaildroneProvider

if TYPE_CHECKING:
    pass

_REGISTRY: dict[str, type[ProviderBase]] = {
    "noaa_pmel": NoaaPmelProvider,
    "saildrone": SaildroneProvider,  # backward-compatible alias
    "r2r": R2RProvider,
    "generic": GenericProvider,
    "cmems": CmemsProvider,
    "emso": EmsoProvider,
    "emodnet": EmodnetProvider,
    "norsoop": NorsoopProvider,
    "oceanlab": OceanlabProvider,
    "ooi": OoiProvider,
    "plocan": PlocanProvider,
}

# Names that resolve via get_provider() but should not appear in list_providers()
_ALIASES: set[str] = {"saildrone"}


def register_provider(name: str, cls: type[ProviderBase]) -> None:
    """Register a new provider class under *name*."""
    _REGISTRY[name.lower().strip()] = cls


def get_provider(name: str) -> ProviderBase:
    key = name.lower().strip()
    if key not in _REGISTRY:
        raise ValueError(f"Unknown provider '{name}'. Available: {sorted(_REGISTRY.keys())}")
    return _REGISTRY[key]()


def list_providers() -> list[str]:
    """Return a sorted list of all registered provider names (excluding aliases)."""
    return sorted(k for k in _REGISTRY if k not in _ALIASES)


def _read_head(file_path: Path, max_lines: int = 20) -> tuple[list[str], list[str]]:
    """Read the first *max_lines* of a file and return (metadata_lines, headers).

    *metadata_lines*: lines that start with ``#`` (e.g. GeoCSV comments).
    *headers*: column names from the first non-comment row.
    """
    metadata_lines: list[str] = []
    headers: list[str] = []
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            for i, raw in enumerate(fh):
                if i >= max_lines:
                    break
                line = raw.strip()
                if line.startswith("#"):
                    metadata_lines.append(line)
                elif not headers:
                    # First non-comment line is column headers
                    headers = [h.strip() for h in line.split(",")]
    except Exception:
        pass
    return metadata_lines, headers


def detect_provider(file_path: Path) -> ProviderBase:
    """Score every registered provider and return the best match.

    Falls back to :class:`GenericProvider` when nothing else claims the data.
    """
    metadata_lines, headers = _read_head(file_path)
    filename = file_path.name

    best_score = 0.0
    best_provider: ProviderBase = GenericProvider()

    for cls in set(_REGISTRY.values()):
        instance = cls()
        try:
            score = instance.detect_confidence(headers, metadata_lines, filename)
        except Exception:
            score = 0.0
        if score > best_score:
            best_score = score
            best_provider = instance

    return best_provider


def detect_or_get_provider(
    name: str | None,
    file_path: Path | None = None,
) -> ProviderBase:
    """Return a provider by explicit *name*, or auto-detect from *file_path*.

    Priority:
    1. Explicit *name* → ``get_provider(name)``
    2. *file_path* given → ``detect_provider(file_path)``
    3. Neither → ``GenericProvider()``
    """
    if name:
        return get_provider(name)
    if file_path is not None:
        return detect_provider(file_path)
    return GenericProvider()
