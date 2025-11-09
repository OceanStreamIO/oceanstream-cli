from __future__ import annotations
from typing import Protocol, Any, Iterable, Literal
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

    def identify_platform(self, filename: str) -> str | None:
        """Return a platform identifier parsed from a raw data filename."""
        ...

    def enrich_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply provider-specific column normalization or derivations.
        Should not drop rows silently.
        """
        ...

    def units_mapping(self, header: Iterable[str], units_row: Iterable[str] | None = None) -> dict[str, Any]:
        """Return units mapping for columns. If units_row provided, use it; else attempt heuristics."""
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
