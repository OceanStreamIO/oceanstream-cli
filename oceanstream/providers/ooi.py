"""Ocean Observatories Initiative (OOI) data provider.

OOI provides data via the M2M REST API.  Arrays include Coastal Endurance
(CE), Coastal Pioneer (CP), Global Irminger Sea (GI), Global Argentine
Basin (GA), Global Station Papa (GP), and Regional Cabled (RS).

This is a stub provider — column mappings and enrichment will be expanded
as OOI integration matures.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

from .base import ProcessingModule


class OoiProvider:
    """Stub provider for OOI (Ocean Observatories Initiative)."""

    name = "ooi"
    supported_modules: list[ProcessingModule] = ["geotrack"]
    is_stationary: bool = False  # mix of fixed + mobile platforms

    def identify_platform(self, filename: str) -> str | None:
        """Extract OOI reference designator from filename if present."""
        # Pattern: CE01ISSM, CP03ISSM, etc. (case-insensitive)
        match = re.search(r"[A-Za-z]{2}\d{2}[A-Za-z]{3,4}", filename)
        return match.group(0).upper() if match else filename.rsplit(".", 1)[0] or None

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
        fname_lower = filename.lower()
        if "ooi" in text or "reference_designator" in text:
            score = max(score, 0.7)
        if "ooi" in fname_lower:
            score = max(score, 0.6)
        if re.search(r"[A-Za-z]{2}\d{2}[A-Za-z]{3,4}", filename):
            score = max(score, 0.5)
        return score
