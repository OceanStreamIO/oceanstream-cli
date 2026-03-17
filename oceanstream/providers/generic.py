"""Generic fallback provider with column auto-detection heuristics.

Works with any CSV that contains latitude, longitude, and time columns
under common naming conventions. Used as fallback when no specific
provider matches the data.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

from .base import ProcessingModule

_LATITUDE_NAMES = {
    "latitude",
    "lat",
    "ship_latitude",
    "y",
    "lat_dd",
    "decimal_lat",
}

_LONGITUDE_NAMES = {
    "longitude",
    "lon",
    "ship_longitude",
    "x",
    "lon_dd",
    "decimal_lon",
}

_TIME_NAMES = {
    "time",
    "datetime",
    "timestamp",
    "date_time",
    "iso_time",
    "date",
    "time_utc",
    "datetime_utc",
}

_NON_ALNUM = re.compile(r"[^0-9a-zA-Z]+")
_MULTI_UNDERSCORE = re.compile(r"_+")


def _normalize_alias(name: str) -> str:
    """Syntactic normalization: convert to snake_case."""
    s = name
    if "_" in s or s.isupper():
        s = s.lower()
    else:
        s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", s).lower()
    s = _NON_ALNUM.sub("_", s)
    s = _MULTI_UNDERSCORE.sub("_", s).strip("_")
    s = re.sub(r"^\d+", "", s)
    return s or name.lower()


def _find_column(columns: Iterable[str], candidates: set[str]) -> str | None:
    """Return the first column whose lowered name matches a candidate set."""
    for col in columns:
        if col.lower().strip() in candidates:
            return col
    return None


class GenericProvider:
    """Fallback provider that auto-detects lat/lon/time columns by name heuristics."""

    name = "generic"
    supported_modules: list[ProcessingModule] = ["geotrack"]
    is_stationary: bool = False

    def identify_platform(self, filename: str) -> str | None:
        """Extract platform identifier from filename (first token before underscore/dash)."""
        name_without_ext = filename.rsplit(".", 1)[0]
        parts = re.split(r"[_\-]", name_without_ext)
        return parts[0] if parts else None

    def enrich_dataframe(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> pd.DataFrame:
        """Rename detected lat/lon/time columns to canonical names."""
        out = df.copy()

        rename_map: dict[str, str] = {}
        lat_col = _find_column(out.columns, _LATITUDE_NAMES)
        if lat_col and lat_col != "latitude":
            rename_map[lat_col] = "latitude"

        lon_col = _find_column(out.columns, _LONGITUDE_NAMES)
        if lon_col and lon_col != "longitude":
            rename_map[lon_col] = "longitude"

        time_col = _find_column(out.columns, _TIME_NAMES)
        if time_col and time_col != "time":
            rename_map[time_col] = "time"

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
        aliases: dict[str, str] = {}
        for col in columns:
            normalized = _normalize_alias(col)
            if normalized != col:
                aliases[col] = normalized
        return aliases

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
        """Generic provider always returns a low baseline confidence (0.1).

        It acts as the fallback when no specific provider claims the data.
        """
        return 0.1
