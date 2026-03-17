"""PLOCAN (Plataforma Oceánica de Canarias) data provider.

PLOCAN is an ocean test site in the Canary Islands operating gliders
(track data) and fixed buoy stations.  Data may also be available
via EMODnet/ERDDAP.

This is a stub provider.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

from .base import ProcessingModule


class PlocanProvider:
    """Stub provider for PLOCAN data."""

    name = "plocan"
    supported_modules: list[ProcessingModule] = ["geotrack"]
    is_stationary: bool = False  # mix of gliders + buoys

    def identify_platform(self, filename: str) -> str | None:
        base = filename.rsplit(".", 1)[0]
        return base if base else None

    def enrich_dataframe(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> pd.DataFrame:
        out = df.copy()
        if "time" in out.columns and not pd.api.types.is_datetime64_any_dtype(out["time"]):
            with pd.option_context("future.no_silent_downcasting", True):
                try:
                    out["time"] = pd.to_datetime(out["time"], errors="coerce", utc=True)
                except Exception:
                    pass
        return out

    def units_mapping(
        self,
        header: Iterable[str],
        units_row: Iterable[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mapping: dict[str, Any] = {}
        if units_row is not None:
            for col, unit in zip(header, units_row):
                u = (unit or "").strip()
                mapping[col] = u if u else None
        return mapping

    def alias_mapping(self, columns: Iterable[str]) -> dict[str, str]:
        return {}

    def parquet_metadata(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"oceanstream:provider": {"name": self.name, "columns": list(df.columns)}}

    def supports_module(self, module: ProcessingModule) -> bool:
        return module in self.supported_modules

    def detect_confidence(
        self,
        headers: list[str],
        metadata_lines: list[str],
        filename: str,
    ) -> float:
        score = 0.0
        text = " ".join(metadata_lines + headers).lower()
        if "plocan" in text:
            score = max(score, 0.7)
        if re.search(r"plocan", filename, re.IGNORECASE):
            score = max(score, 0.5)
        return score
