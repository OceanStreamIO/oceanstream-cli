"""EMODnet Physics data provider.

EMODnet Physics aggregates in-situ observations (temperature, salinity,
currents, waves, sea level) with an ERDDAP server at
``erddap.emodnet-physics.eu`` hosting ~876 datasets.

Only the **Physics** thematic lot is in scope.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

from .base import ProcessingModule

EMODNET_PHYSICS_ERDDAP_URL = "https://erddap.emodnet-physics.eu/erddap"


class EmodnetProvider:
    """Provider for EMODnet Physics ERDDAP data.

    Handles a mix of fixed platforms (buoys, tide gauges) and mobile
    platforms (drifters, Argo floats, gliders).  ``is_stationary`` defaults
    to ``False`` because many datasets contain mobile tracks.
    """

    name = "emodnet"
    supported_modules: list[ProcessingModule] = ["geotrack"]
    is_stationary: bool = False

    def identify_platform(self, filename: str) -> str | None:
        """Extract platform or dataset identifier from filename."""
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

        # Drop EMODnet quality-flag columns (QV:*) from the main data
        # and keep them as metadata if needed
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
        """EMODnet ERDDAP typically uses CF-style names."""
        return {}

    def parquet_metadata(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "oceanstream:provider": {
                "name": self.name,
                "erddap_server": EMODNET_PHYSICS_ERDDAP_URL,
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
        if "emodnet" in text:
            score = max(score, 0.7)
        lower_headers = {h.lower() for h in headers}
        emodnet_cols = {"local_cdi_id", "edmo_code", "dc:edmo_code"}
        if lower_headers & emodnet_cols:
            score = max(score, 0.75)
        # QV: quality flag columns
        if any(h.upper().startswith("QV:") for h in headers):
            score = max(score, 0.6)
        if re.search(r"emodnet", filename, re.IGNORECASE):
            score = max(score, 0.5)
        return score
