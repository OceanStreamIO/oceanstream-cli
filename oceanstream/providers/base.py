from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, Protocol

import pandas as pd

ProcessingModule = Literal["geotrack", "echodata", "multibeam", "adcp"]


class ProviderBase(Protocol):
    """Abstract protocol for data providers.

    A provider encapsulates source-specific logic: filename parsing, units extraction,
    alias generation, and optional record post-processing. It may also expose
    provider-specific metadata blocks destined for Parquet key-value storage.

    Providers can support one or more processing modules (geotrack, echodata, multibeam, adcp).
    """

    name: str
    supported_modules: list[ProcessingModule]
    is_stationary: bool

    def identify_platform(self, filename: str) -> str | None:
        """Return a platform identifier parsed from a raw data filename."""
        ...

    def enrich_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply provider-specific column normalization or derivations.
        Should not drop rows silently.
        """
        ...

    def units_mapping(
        self,
        header: Iterable[str],
        units_row: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Return units mapping for columns.

        If *units_row* is provided, use it; otherwise attempt heuristics.
        """
        ...

    def alias_mapping(self, columns: Iterable[str]) -> dict[str, str]:
        """Return alias mapping for columns (only differing entries)."""
        ...

    def parquet_metadata(self, df: pd.DataFrame) -> dict[str, Any]:
        """Return provider-specific metadata to embed under oceanstream:* keys."""
        ...

    def supports_module(self, module: ProcessingModule) -> bool:
        """Check if this provider supports the given processing module."""
        ...

    def detect_confidence(
        self,
        headers: list[str],
        metadata_lines: list[str],
        filename: str,
    ) -> float:
        """Return 0.0–1.0 confidence that this provider matches the data.

        Used by auto-detection in ``factory.detect_provider`` to score all
        registered providers and pick the best match.
        """
        ...
