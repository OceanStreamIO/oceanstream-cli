"""OceanLab Observatory data provider (SINTEF Ocean / NTNU).

OceanLab operates two fixed buoy sites near Trondheim:
- Munkholmen (63°27.45'N, 10°22.33'E, 80 m depth)
- Ingdalen (63°27.7'N, 9°57.0'E, 530 m depth)

Data access is via the OceanLab data portal or API
(``oceanlabobservatory.no``).

Observatory-specific processing
-------------------------------
Each site may have instruments that log data in site-specific formats.
The ``OCEANLAB_OBSERVATORIES`` registry maps site keys to instrument
configurations including column mappings and fixed metadata.

Munkholmen hosts a Sea-Bird SBE19plus V2 CTD that writes a single-row
``latest_ctd.csv`` to an SMB share.  ``parse_ctd_latest()`` reads that
file using the SBE19plus column mapping defined in this module and
returns a normalised record dict suitable for telemetry.
"""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from .base import ProcessingModule

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Site registry
# ------------------------------------------------------------------

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

# ------------------------------------------------------------------
# Observatory instrument configurations
# ------------------------------------------------------------------

# Column mapping loaded from the SBE19plus sensor definition.
# Fallback hard-coded if the JSON is unavailable.
_SBE19PLUS_COLUMN_MAP_FALLBACK: dict[str, str] = {
    "Temperature": "temperature",
    "Conductivity": "conductivity",
    "Pressure": "pressure",
    "Salinity": "salinity",
    "SBE63": "oxygen",
    "SBE63Temperature": "oxygen_temperature",
    "Timestamp": "time",
    "Volt0": "volt0",
    "Volt1": "volt1",
    "Volt2": "volt2",
    "Volt4": "volt4",
    "Volt5": "volt5",
}


def _load_sbe19plus_column_map() -> dict[str, str]:
    """Load column_mapping from the SBE19plus sensor definition JSON."""
    sensor_json = (
        Path(__file__).resolve().parent.parent
        / "sensors" / "definitions" / "sbe19plus" / "sensor.json"
    )
    try:
        with open(sensor_json, encoding="utf-8") as fh:
            defn = json.load(fh)
        mapping = defn.get("column_mapping")
        if isinstance(mapping, dict) and mapping:
            return mapping
    except Exception:
        pass
    return _SBE19PLUS_COLUMN_MAP_FALLBACK


_SBE19PLUS_COLUMN_MAP: dict[str, str] = _load_sbe19plus_column_map()


OCEANLAB_OBSERVATORIES: dict[str, dict[str, Any]] = {
    "munkholmen": {
        "site": OCEANLAB_SITES["munkholmen"],
        "instruments": {
            "ctd": {
                "sensor_id": "sbe19plus",
                "serial_number": "01908153",
                "column_mapping": _SBE19PLUS_COLUMN_MAP,
            },
            "adcp": {
                "sensor_id": "nortek-ad2cp",
                "model": "Signature",
                "default_salinity": 35.0,
            },
        },
    },
}


# ------------------------------------------------------------------
# Observatory record parsing
# ------------------------------------------------------------------


def parse_ctd_latest(
    file_path: Path,
    observatory: str = "munkholmen",
) -> dict[str, Any] | None:
    """Read a single-row CTD CSV and return a normalised record.

    Uses the column mapping from the observatory's CTD instrument
    configuration.  The observatory's fixed latitude/longitude are
    injected into the record.

    Parameters
    ----------
    file_path
        Path to a CSV file with a header row and one data row
        (e.g. ``latest_ctd.csv``).
    observatory
        Key into ``OCEANLAB_OBSERVATORIES`` (default ``"munkholmen"``).

    Returns
    -------
    dict or None
        Normalised record with canonical column names, or ``None``
        if the file cannot be read or has no data.
    """
    obs = OCEANLAB_OBSERVATORIES.get(observatory)
    if obs is None:
        logger.warning("Unknown observatory: %s", observatory)
        return None

    col_map = obs["instruments"]["ctd"]["column_mapping"]
    site = obs["site"]

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            reader = csv.DictReader(fh)
            row = next(reader, None)
    except (OSError, StopIteration) as exc:
        logger.debug("Cannot read CTD file %s: %s", file_path, exc)
        return None

    if row is None:
        return None

    record: dict[str, Any] = {
        "source": f"oceanlab_{observatory}",
        "file_type": "ctd_csv",
        "observatory": observatory,
        "latitude": site["latitude"],
        "longitude": site["longitude"],
    }

    for raw_col, value in row.items():
        if raw_col is None:
            continue
        key = col_map.get(raw_col.strip(), raw_col.strip().lower())
        value = value.strip() if isinstance(value, str) else value

        if key == "time":
            record[key] = value
            continue

        try:
            numeric = float(value)
            record[key] = numeric
        except (ValueError, TypeError):
            record[key] = value

    if not record.get("time"):
        return None

    return record


def get_adcp_defaults(observatory: str = "munkholmen") -> dict[str, Any]:
    """Return ADCP processing defaults for an OceanLab observatory.

    Parameters
    ----------
    observatory
        Key into ``OCEANLAB_OBSERVATORIES`` (default ``"munkholmen"``).

    Returns
    -------
    dict
        Keys: ``salinity``, ``latitude``, ``longitude``.
        Empty dict if the observatory has no ADCP instrument.
    """
    obs = OCEANLAB_OBSERVATORIES.get(observatory)
    if obs is None or "adcp" not in obs.get("instruments", {}):
        return {}
    adcp = obs["instruments"]["adcp"]
    site = obs["site"]
    return {
        "salinity": adcp.get("default_salinity", 35.0),
        "latitude": site["latitude"],
        "longitude": site["longitude"],
    }


class OceanlabProvider:
    """Provider for OceanLab Observatory (SINTEF Ocean / NTNU).

    Both stations are fixed buoy observatories → stationary time-series.
    """

    name = "oceanlab"
    supported_modules: list[ProcessingModule] = ["geotrack", "adcp"]
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

        # Detect which observatory this data belongs to
        site_key = self._detect_site(out, metadata)

        # Apply instrument column mappings
        obs = OCEANLAB_OBSERVATORIES.get(site_key) if site_key else None
        if obs is not None:
            # Apply CTD column mappings if present
            ctd_map = obs["instruments"].get("ctd", {}).get("column_mapping", {})
            rename = {k: v for k, v in ctd_map.items() if k in out.columns and k != v}
            if rename:
                out = out.rename(columns=rename)

            # Apply ADCP column mappings if present
            adcp_map = obs["instruments"].get("adcp", {}).get("column_mapping", {})
            rename_adcp = {k: v for k, v in adcp_map.items() if k in out.columns and k != v}
            if rename_adcp:
                out = out.rename(columns=rename_adcp)

            # Inject fixed site coordinates if not already present
            site = obs["site"]
            if "latitude" not in out.columns:
                out["latitude"] = site["latitude"]
            if "longitude" not in out.columns:
                out["longitude"] = site["longitude"]

        if "platform_id" in out.columns:
            out["platform_id"] = out["platform_id"].astype(str)

        if "time" in out.columns and not pd.api.types.is_datetime64_any_dtype(out["time"]):
            with pd.option_context("future.no_silent_downcasting", True):
                try:
                    out["time"] = pd.to_datetime(out["time"], errors="coerce", utc=True)
                except Exception:
                    pass

        return out

    def _detect_site(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> str | None:
        """Try to detect the OceanLab site from metadata or column patterns."""
        if metadata:
            site = metadata.get("observatory") or metadata.get("site")
            if isinstance(site, str) and site.lower() in OCEANLAB_SITES:
                return site.lower()

        # Heuristic: SBE19plus CSV columns → Munkholmen
        sbe19_cols = {"Temperature", "Conductivity", "Pressure", "SBE63", "SBE63Temperature"}
        if sbe19_cols.issubset(set(df.columns)):
            return "munkholmen"

        # Heuristic: AD2CP-derived columns → Munkholmen
        ad2cp_cols = {"Sv", "frequency_khz", "sound_speed"}
        if ad2cp_cols.issubset(set(df.columns)):
            return "munkholmen"

        return None

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
        """Return alias mapping using the SBE19plus column map for known columns."""
        col_set = set(columns)
        return {k: v for k, v in _SBE19PLUS_COLUMN_MAP.items() if k in col_set and k != v}

    def detect_confidence(
        self,
        headers: list[str],
        metadata_lines: list[str],
        filename: str,
    ) -> float:
        """Return confidence that this provider matches the data."""
        score = 0.0

        # Filename-based detection
        lower = filename.lower()
        for key in OCEANLAB_SITES:
            if key in lower:
                score += 0.5

        if "oceanlab" in lower:
            score += 0.4

        # Header-based detection: SBE19plus CTD columns
        hdr_set = {h.strip() for h in headers}
        sbe19_cols = {"Temperature", "Conductivity", "Pressure", "Salinity", "SBE63"}
        if sbe19_cols.issubset(hdr_set):
            score += 0.3

        # AD2CP-derived columns
        ad2cp_cols = {"Sv", "frequency_khz", "sound_speed"}
        if ad2cp_cols.issubset(hdr_set):
            score += 0.3

        return min(score, 1.0)

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
        if lower.endswith(".ad2cp") and any(site in lower for site in OCEANLAB_SITES):
            score = max(score, 0.7)
        return score
