"""EMSO-ERIC observatory data provider.

EMSO operates fixed ocean observatories across Europe with ERDDAP data
access at ``erddap.emso.eu``.  Stations include OBSEA, SmartBay,
E1M3A, Station M, Pylos, and others.

Typical workflow: users download CSV from ERDDAP, then process locally
with OceanStream.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

from .base import ProcessingModule

EMSO_ERDDAP_URL = "https://erddap.emso.eu/erddap"

# Known EMSO observatory sites
EMSO_SITES = {
    "OBSEA",
    "SmartBay",
    "E1M3A",
    "E2M3A",
    "W1M3A",
    "PYLOS",
    "NEMO",
    "PAP",
    "ESTOC",
    "EMSO_AZORES",
    "STATION_M",
    "NORDMSO",
    "NORSMSO",
}


class EmsoProvider:
    """Provider for EMSO-ERIC observatory ERDDAP data.

    All EMSO observatories are stationary — each has a fixed lat/lon.
    """

    name = "emso"
    supported_modules: list[ProcessingModule] = ["geotrack"]
    is_stationary: bool = True

    def identify_platform(self, filename: str) -> str | None:
        """Extract EMSO site name from filename/dataset ID.

        EMSO dataset IDs pattern: ``{SITE}_{SENSOR}_{PERIOD}``
        e.g. ``EMSO_OBSEA_CTD_30min`` → ``OBSEA``
        """
        base = filename.rsplit(".", 1)[0]
        parts = base.upper().split("_")
        # Skip leading 'EMSO' token if present
        tokens = [p for p in parts if p != "EMSO"]
        if tokens:
            return tokens[0]
        return base

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

        # EMSO ERDDAP already uses standard names – minimal enrichment
        if "depth" in out.columns:
            out["depth"] = pd.to_numeric(out["depth"], errors="coerce")

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
        """EMSO ERDDAP typically uses CF standard names already."""
        return {}

    def parquet_metadata(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "oceanstream:provider": {
                "name": self.name,
                "erddap_server": EMSO_ERDDAP_URL,
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
        if "emso" in text:
            score = max(score, 0.7)
        fn_upper = filename.upper()
        for site in EMSO_SITES:
            if site in fn_upper:
                score = max(score, 0.6)
                break
        if re.match(r"EMSO_", filename, re.IGNORECASE):
            score = max(score, 0.8)
        return score
