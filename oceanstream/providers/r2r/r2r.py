"""R2R (Rolling Deck to Repository) data provider.

This module hosts the :class:`R2RProvider` class. It is kept separate
from the archive/metadata helpers to keep the public provider API
clear while still allowing the R2R-specific utilities to live in the
same subpackage.
"""

from pathlib import Path
import re
from typing import Any, Dict, List, Literal

import pandas as pd

from oceanstream.providers.r2r.r2r_archive import (
    R2RArchiveLayout,
    extract_r2r_archive,
    find_r2r_archives,
)
from oceanstream.providers.r2r.r2r_metadata import (
    R2RFileInfo,
    R2RSensorInfo,
    parse_bag_info,
    parse_file_info,
)
from oceanstream.sensors.processor_base import SensorDescriptor
from oceanstream.sensors.processors import get_sensor_processor

from ..base import ProcessingModule, ProviderBase


class R2RProvider(ProviderBase):
    """Provider for R2R (Rolling Deck to Repository) GeoCSV data."""

    name: str = "r2r"
    supported_modules: List[ProcessingModule] = ["geotrack"]

    # Column name mappings: R2R name -> oceanstream standard
    COLUMN_MAPPINGS = {
        # R2R uses "ship_*" prefix for vessel position
        "ship_longitude": "longitude",
        "ship_latitude": "latitude",
        "ship_depth": "depth",

        # R2R uses "iso_time" for timestamps
        "iso_time": "time",

        # GPS quality indicators
        "nmea_quality": "gps_quality",
        "nsv": "num_satellites",
        "hdop": "horizontal_dilution",

        # Navigation parameters
        "speed_made_good": "speed_over_ground",
        "course_made_good": "course_over_ground",
        "antenna_height": "gps_antenna_height",
    }

    # Units mappings for standard columns
    STANDARD_UNITS = {
        "longitude": "degree_east",
        "latitude": "degree_north",
        "depth": "meters",
        "time": "ISO_8601",
        "speed_over_ground": "meters_per_second",
        "course_over_ground": "degree",
        "gps_antenna_height": "meters",
    }

    def supports_module(self, module: ProcessingModule) -> bool:
        """Return True if this provider supports the given module.

        For R2R we currently only support the geotrack module, but the
        method is implemented explicitly for clarity and to satisfy the
        ProviderBase protocol used in tests.
        """

        return module in self.supported_modules

    def identify_platform(self, filename: str) -> str | None:
        """Extract platform/cruise ID from an R2R filename.

        R2R filenames typically start with a cruise ID, e.g.::

            FK161229_607994_r2rnav.geocsv -> FK161229
            AT42-10_some_data.geocsv      -> AT42-10
            NBP1402_ctd_001.geocsv        -> NBP1402
        """

        # R2R cruise IDs are typically at start of filename before first underscore
        # Format: <CruiseID>_<EventID>_<InstrumentType>.geocsv
        if "_" in filename:
            cruise_id = filename.split("_", 1)[0]
            # Validate it looks like a cruise ID (letters + numbers, possibly with hyphen)
            if re.match(r"^[A-Z]{2,4}\d{2,6}(-\d+)?$", cruise_id, re.IGNORECASE):
                return cruise_id.upper()

        return None

    def inspect_archives(
        self,
        archives_root: Path,
        work_root: Path,
        provider_id: str | None = None,
    ) -> list[SensorDescriptor]:
        """Inspect all R2R archives under a root directory.

        This method is intentionally side-effect free with respect to the
        main ingestion pipeline. It is designed for external tooling
        (for example, a catalogue update script) that wants to discover
        which sensors are present in a collection of R2R archives.
        """

        provider_id = provider_id or self.name
        descriptors: list[SensorDescriptor] = []

        for archive in find_r2r_archives(archives_root):
            layout: R2RArchiveLayout = extract_r2r_archive(archive, work_root)

            if layout.file_info_path is not None:
                file_info = parse_file_info(layout.file_info_path)
            else:
                file_info = R2RFileInfo()

            if layout.bag_info_path is not None:
                sensor_info = parse_bag_info(layout.bag_info_path)
            else:
                sensor_info = R2RSensorInfo()

            if layout.data_dir is None:
                # Nothing to inspect for this archive.
                continue

            sensor_type = sensor_info.sensor_type or "example"
            processor = get_sensor_processor(sensor_type)
            if processor is None:
                # Unknown sensor type for now – skip rather than
                # failing hard so the caller can still inspect archives
                # with partially supported contents.
                continue

            descriptor = processor(layout.data_dir, file_info, sensor_info, provider_id)
            descriptors.append(descriptor)

        return descriptors

    def enrich_dataframe(
        self,
        df: pd.DataFrame,
        metadata: Dict[str, Any] | None = None,
    ) -> pd.DataFrame:
        """Enrich and standardize an R2R dataframe."""

        df = df.copy()

        # Rename columns to oceanstream standards
        rename_map = {
            col: new_col
            for col, new_col in self.COLUMN_MAPPINGS.items()
            if col in df.columns
        }
        if rename_map:
            df = df.rename(columns=rename_map)

        # Ensure required columns exist
        if "latitude" in df.columns and "longitude" in df.columns:
            # Convert to numeric
            df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
            df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")

            # Drop invalid coordinates
            df = df.dropna(subset=["latitude", "longitude"])

            # Validate coordinate ranges
            df = df[(df["latitude"] >= -90) & (df["latitude"] <= 90)]
            df = df[(df["longitude"] >= -180) & (df["longitude"] <= 180)]

        # Parse time column
        if "time" in df.columns and not pd.api.types.is_datetime64_any_dtype(df["time"]):
            try:
                df["time"] = pd.to_datetime(df["time"], errors="coerce")
            except Exception:
                # If parsing fails we leave the column as-is.
                pass

        # Convert numeric columns
        numeric_cols = [
            "depth",
            "gps_quality",
            "num_satellites",
            "horizontal_dilution",
            "speed_over_ground",
            "course_over_ground",
            "gps_antenna_height",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # Add platform_id from metadata if available
        if metadata and "cruise_id" in metadata and "platform_id" not in df.columns:
            df["platform_id"] = metadata["cruise_id"]

        return df

    def units_mapping(
        self,
        header: List[str],
        units_row: List[str] | None = None,
        metadata: Dict[str, str] | None = None,
    ) -> Dict[str, str]:
        """Extract units mapping from GeoCSV metadata or data."""

        units_map: Dict[str, str] = {}

        # Parse from GeoCSV metadata if available
        if metadata and "field_unit" in metadata:
            # field_unit format: "unit1,unit2,unit3,..."
            units_str = metadata["field_unit"]
            unit_parts = [u.strip() for u in units_str.split(",")]

            # Map units to columns
            for col, unit in zip(header, unit_parts):
                if unit and unit != "(unitless)":
                    units_map[col] = unit

        # Add standard units for renamed columns
        for col, unit in self.STANDARD_UNITS.items():
            if col in header or col in units_map:
                units_map[col] = unit

        return units_map

    def alias_mapping(self, columns: List[str]) -> Dict[str, str]:
        """Get column name aliases for R2R data."""

        return {
            original: standard
            for original, standard in self.COLUMN_MAPPINGS.items()
            if original in columns
        }

    def parquet_metadata(
        self,
        df: pd.DataFrame,
        metadata: Dict[str, str] | None = None,
    ) -> Dict[str, str]:
        """Generate Parquet metadata from R2R GeoCSV metadata.

        The returned keys are merged into the generic oceanstream
        metadata block by the geotrack pipeline. In particular, the
        provider name is recorded under ``oceanstream:provider`` so
        tests can verify that the R2R provider was used.
        """

        md: Dict[str, str] = {}

        if metadata is None:
            metadata = {}

        # Always record the provider for downstream discovery.
        md["oceanstream:provider"] = self.name

        cruise_id = metadata.get("cruise_id")
        if cruise_id:
            md["r2r:cruise_id"] = cruise_id

        doi = metadata.get("doi") or metadata.get("dataset_doi")
        if doi:
            md["r2r:doi"] = doi

        return md
