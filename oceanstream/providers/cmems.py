"""Copernicus Marine Service (CMEMS) In Situ TAC provider.

CMEMS aggregates in-situ observations from multi-platform networks across
European regional seas and the global ocean.  Data is downloaded via the
``copernicusmarine`` toolbox (``pip install copernicusmarine``).

The CSV export uses a **long (melted) format** where each row represents
a single variable observation::

    variable,platform_id,platform_type,time,longitude,latitude,depth,...,value,value_qc,...

This provider can optionally pivot the long format into a wide table
suitable for the geotrack pipeline.

Reference products:
- ``INSITU_GLO_PHYBGCWAV_DISCRETE_MYNRT_013_030`` — Global
- ``INSITU_NWS_PHYBGCWAV_DISCRETE_MYNRT_013_036`` — NW Shelf
- ``INSITU_BAL_PHYBGCWAV_DISCRETE_MYNRT_013_032`` — Baltic
- ``INSITU_MED_PHYBGCWAV_DISCRETE_MYNRT_013_035`` — Mediterranean
- ``INSITU_IBI_PHYBGCWAV_DISCRETE_MYNRT_013_033`` — Iberian Biscay Ireland
- ``INSITU_ARC_PHYBGCWAV_DISCRETE_MYNRT_013_031`` — Arctic
- ``INSITU_BLK_PHYBGCWAV_DISCRETE_MYNRT_013_034`` — Black Sea
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

import pandas as pd

from .base import ProcessingModule

# CMEMS In Situ TAC dataset ID pattern
CMEMS_DATASET_PATTERN = re.compile(
    r"cmems_obs-ins_(?P<region>\w+)_(?P<vars>\w+)_(?P<freq>\w+)_na_irr",
)

# CMEMS platform type codes
CMEMS_PLATFORM_TYPES: dict[str, str] = {
    "MO": "mooring",
    "FB": "ferrybox",
    "TS": "thermosalinometer",
    "DB": "drifting_buoy",
    "TG": "tide_gauge",
    "GL": "glider",
    "PF": "profiling_float",
    "CT": "ctd",
    "SM": "sea_mammal",
    "ML": "mini_logger",
    "RA": "radar",
    "DC": "drifting_buoy",
}

# CMEMS In Situ TAC variable names → OceanStream canonical
CMEMS_VARIABLE_MAPPINGS: dict[str, str] = {
    "TEMP": "temperature",
    "PSAL": "salinity",
    "DOX1": "dissolved_oxygen",
    "DOX2": "dissolved_oxygen_2",
    "CPHL": "chlorophyll_fluorescence",
    "TURB": "turbidity",
    "SLEV": "sea_level",
    "EWCT": "eastward_current",
    "NSCT": "northward_current",
    "HCSP": "current_speed",
    "HCDT": "current_direction",
    "VHM0": "significant_wave_height",
    "VTPK": "wave_peak_period",
    "VMDR": "wave_direction",
    "NTRI": "nitrate",
    "PHPH": "ph",
    "PHOS": "phosphate",
    "SLCA": "silicate",
    "AMON": "ammonium",
}

# Columns that identify the CMEMS long-format CSV
CMEMS_SIGNATURE_COLUMNS = {
    "variable",
    "platform_id",
    "platform_type",
    "value",
    "value_qc",
    "product_doi",
}

# CMEMS product DOI prefix
CMEMS_DOI_PREFIX = "https://doi.org/10.48670/"


class CmemsProvider:
    """Provider for Copernicus Marine (CMEMS) In Situ TAC data.

    Handles any CSV downloaded via the ``copernicusmarine`` toolbox
    ``subset`` command with ``--file-format csv``.
    """

    name = "cmems"
    supported_modules: list[ProcessingModule] = ["geotrack"]
    is_stationary: bool = False

    def identify_platform(self, filename: str) -> str | None:
        """Extract a platform hint from the filename.

        CMEMS filenames are user-chosen, so fall back to the dataset
        region if a ``cmems_obs-ins_<region>`` pattern is found.
        """
        m = CMEMS_DATASET_PATTERN.search(filename.lower())
        if m:
            return m.group("region")
        lower = filename.lower().rsplit(".", 1)[0]
        parts = re.split(r"[_\-]", lower)
        return parts[0] if parts else None

    def enrich_dataframe(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> pd.DataFrame:
        """Normalise CMEMS long-format CSV into a wide table.

        The ``copernicusmarine`` CSV stores one row per
        (variable, platform, time, location) observation.  This method
        pivots the data so each variable becomes its own column, which
        is the format expected by the geotrack pipeline.

        Quality flags (``value_qc``) are preserved as ``<var>_qc``
        columns.
        """
        out = df.copy()

        # Detect long format by presence of the 'variable' column
        if "variable" in out.columns and "value" in out.columns:
            out = self._pivot_long_to_wide(out)

        # Rename variables to canonical names
        rename_map = {
            col: CMEMS_VARIABLE_MAPPINGS[col]
            for col in out.columns
            if col in CMEMS_VARIABLE_MAPPINGS
        }
        if rename_map:
            out = out.rename(columns=rename_map)

        if "platform_id" in out.columns:
            out["platform_id"] = out["platform_id"].astype(str)

        if "platform_type" in out.columns:
            out["platform_type"] = out["platform_type"].map(
                lambda x: CMEMS_PLATFORM_TYPES.get(str(x), str(x))
            )

        if "time" in out.columns and not pd.api.types.is_datetime64_any_dtype(
            out["time"]
        ):
            with pd.option_context("future.no_silent_downcasting", True):
                try:
                    out["time"] = pd.to_datetime(
                        out["time"], errors="coerce", utc=True
                    )
                except Exception:
                    pass

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
        return {
            col: CMEMS_VARIABLE_MAPPINGS[col]
            for col in columns
            if col in CMEMS_VARIABLE_MAPPINGS
        }

    def parquet_metadata(
        self, df: pd.DataFrame, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "name": self.name,
            "source": "Copernicus Marine Service (CMEMS) In Situ TAC",
            "columns": list(df.columns),
        }
        if "product_doi" in df.columns:
            dois = df["product_doi"].dropna().unique().tolist()
            if dois:
                meta["product_doi"] = dois
        return {"oceanstream:provider": meta}

    def supports_module(self, module: ProcessingModule) -> bool:
        return module in self.supported_modules

    def detect_confidence(
        self,
        headers: list[str],
        metadata_lines: list[str],
        filename: str,
    ) -> float:
        """Score how likely this data is from CMEMS.

        Strong signals:
        - CMEMS signature columns (variable, platform_type, value_qc, product_doi)
        - ``cmems`` in filename or metadata
        - ``doi.org/10.48670`` DOI prefix in data
        """
        score = 0.0

        lower_headers = {h.lower() for h in headers}

        # Strongest signal: CMEMS long-format signature columns
        sig_matches = lower_headers & {c.lower() for c in CMEMS_SIGNATURE_COLUMNS}
        if len(sig_matches) >= 4:
            score = max(score, 0.85)
        elif len(sig_matches) >= 3:
            score = max(score, 0.7)

        # Filename contains cmems
        if re.search(r"cmems", filename, re.IGNORECASE):
            score = max(score, 0.75)

        # Metadata / header text mentions cmems or copernicus marine
        text = " ".join(metadata_lines + headers).lower()
        if "cmems" in text or "copernicus marine" in text:
            score = max(score, 0.7)

        # DOI prefix in metadata
        if CMEMS_DOI_PREFIX in " ".join(metadata_lines):
            score = max(score, 0.8)

        # Dataset ID pattern in filename
        if CMEMS_DATASET_PATTERN.search(filename.lower()):
            score = max(score, 0.8)

        return score

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pivot_long_to_wide(df: pd.DataFrame) -> pd.DataFrame:
        """Pivot CMEMS long-format CSV to wide format.

        Input columns: variable, platform_id, platform_type, time,
        longitude, latitude, depth, value, value_qc, institution, ...

        Output: one row per (platform_id, time, longitude, latitude, depth)
        with each variable as a separate column plus ``<var>_qc``.
        """
        index_cols = [
            c
            for c in [
                "platform_id",
                "platform_type",
                "time",
                "longitude",
                "latitude",
                "depth",
                "institution",
                "product_doi",
            ]
            if c in df.columns
        ]

        # Pivot values
        wide_val = df.pivot_table(
            index=index_cols,
            columns="variable",
            values="value",
            aggfunc="first",
        )
        wide_val.columns = [str(c) for c in wide_val.columns]

        # Pivot QC flags
        if "value_qc" in df.columns:
            wide_qc = df.pivot_table(
                index=index_cols,
                columns="variable",
                values="value_qc",
                aggfunc="first",
            )
            wide_qc.columns = [f"{c}_qc" for c in wide_qc.columns]
            wide = wide_val.join(wide_qc)
        else:
            wide = wide_val

        return wide.reset_index()
