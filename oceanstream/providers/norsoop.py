"""NorSOOP (Norwegian Ships of Opportunity Program) FerryBox provider.

NorSOOP is operated by NIVA with data served via THREDDS at
``thredds.niva.no``.  Ships include Hurtigruten fleet, Color Line,
Norbjørn, Norröna, and others.

Typical workflow: users download NetCDF from THREDDS / convert to CSV,
then process locally with OceanStream.  ``erddapy`` is an optional
dependency for direct THREDDS/OPeNDAP access.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

from .base import ProcessingModule

NORSOOP_THREDDS_URL = "https://thredds.niva.no/thredds"

# Known NorSOOP vessels
NORSOOP_VESSELS: dict[str, str] = {
    "richard_with": "M/S Richard With",
    "trollfjord": "M/S Trollfjord",
    "vesteralen": "M/S Vesterålen",
    "color_fantasy": "M/S Color Fantasy",
    "color_hybrid": "M/S Color Hybrid",
    "norbjorn": "M/S Norbjørn",
    "norrona": "M/S Norröna",
    "connector": "M/S Connector",
}

# Column mappings: FerryBox conventions → OceanStream canonical
FERRYBOX_COLUMN_MAPPINGS: dict[str, str] = {
    "TEMP": "temperature",
    "PSAL": "salinity",
    "DOX1": "dissolved_oxygen",
    "DOX2": "dissolved_oxygen_2",
    "CPHL": "chlorophyll_fluorescence",
    "TURB": "turbidity",
    "PCO2": "pco2",
    "PHPH": "ph",
    "CDOM": "cdom_fluorescence",
    "WSPD": "wind_speed",
    "WDIR": "wind_direction",
    "ATMP": "air_temperature",
    "RELH": "relative_humidity",
    "ATMS": "barometric_pressure",
}


class NorsoopProvider:
    """Provider for NIVA NorSOOP FerryBox data.

    Track data from ships of opportunity — mobile vessels with
    inline sensors.
    """

    name = "norsoop"
    supported_modules: list[ProcessingModule] = ["geotrack"]
    is_stationary: bool = False

    def identify_platform(self, filename: str) -> str | None:
        """Extract vessel name from filename.

        Filenames may contain vessel names (e.g. ``trollfjord_2023.csv``).
        """
        lower = filename.lower().rsplit(".", 1)[0]
        for key in NORSOOP_VESSELS:
            if key in lower:
                return key
        parts = re.split(r"[_\-]", lower)
        return parts[0] if parts else None

    def enrich_dataframe(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> pd.DataFrame:
        out = df.copy()

        # Apply FerryBox column renaming
        rename_map = {
            col: FERRYBOX_COLUMN_MAPPINGS[col]
            for col in out.columns
            if col in FERRYBOX_COLUMN_MAPPINGS
        }
        if rename_map:
            out = out.rename(columns=rename_map)

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
        return {
            col: FERRYBOX_COLUMN_MAPPINGS[col] for col in columns if col in FERRYBOX_COLUMN_MAPPINGS
        }

    def parquet_metadata(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {
            "oceanstream:provider": {
                "name": self.name,
                "thredds_server": NORSOOP_THREDDS_URL,
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
        if "norsoop" in text or "niva" in text or "ferrybox" in text:
            score = max(score, 0.7)
        lower = filename.lower()
        for vessel in NORSOOP_VESSELS:
            if vessel in lower:
                score = max(score, 0.65)
                break
        lower_headers = {h.lower() for h in headers}
        _MIN_FERRYBOX_COL_MATCHES = 3
        fb_cols = {"temp", "psal", "dox1", "cphl", "turb"}
        matches = lower_headers & fb_cols
        if len(matches) >= _MIN_FERRYBOX_COL_MATCHES:
            score = max(score, 0.6)
        return score
