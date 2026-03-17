"""OceanLab Observatory data provider (SINTEF Ocean / NTNU).

OceanLab operates two fixed buoy sites near Trondheim:
- Munkholmen (63°27.45'N, 10°22.33'E, 80 m depth)
- Ingdalen (63°27.7'N, 9°57.0'E, 530 m depth)

Data access is via the OceanLab data portal or API
(``oceanlabobservatory.no``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from .base import ProcessingModule

OCEANLAB_SITES: dict[str, dict[str, Any]] = {
    "munkholmen": {
        "name": "Munkholmen",
        "latitude": 63.4575,
        "longitude": 10.3722,
        "depth_m": 80,
    },
    "ingdalen": {
        "name": "Ingdalen",
        "latitude": 63.4617,
        "longitude": 9.9500,
        "depth_m": 530,
    },
}


class OceanlabProvider:
    """Provider for OceanLab Observatory (SINTEF Ocean / NTNU).

    Both stations are fixed buoy observatories → stationary time-series.
    """

    name = "oceanlab"
    supported_modules: list[ProcessingModule] = ["geotrack"]
    is_stationary: bool = True

    def identify_platform(self, filename: str) -> str | None:
        """Extract site name from filename (Munkholmen or Ingdalen)."""
        lower = filename.lower()
        for key in OCEANLAB_SITES:
            if key in lower:
                return OCEANLAB_SITES[key]["name"]
        base = filename.rsplit(".", 1)[0]
        return base if base else None

    def enrich_dataframe(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> pd.DataFrame:
        out = df.copy()

        if "platform_id" in out.columns:
            out["platform_id"] = out["platform_id"].astype(str)

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
        return {
            "oceanstream:provider": {
                "name": self.name,
                "columns": list(df.columns),
            }
        }

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
        if "oceanlab" in text or "sintef" in text:
            score = max(score, 0.7)
        lower = filename.lower()
        for site in OCEANLAB_SITES:
            if site in lower:
                score = max(score, 0.65)
                break
        return score
