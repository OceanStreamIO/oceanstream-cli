from __future__ import annotations
from typing import Any, Iterable
import re
import pandas as pd

from .base import ProviderBase, ProcessingModule
from .models import OceanographicMeasurement

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^0-9a-zA-Z]+")
_MULTI_UNDERSCORE = re.compile(r"_+")


def _normalize_alias(name: str) -> str:
    """Syntactic normalization: convert to snake_case."""
    # Handle common cases first: keep existing underscores, and all-caps acronyms, by just lowering
    s = name
    if "_" in s or s.isupper():
        s = s.lower()
    else:
        # Insert underscores between a lower/number followed by Upper (camelCase to snake_case)
        s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", s).lower()
    # Replace non-alnum with underscores and collapse multiples
    s = _NON_ALNUM.sub("_", s)
    s = _MULTI_UNDERSCORE.sub("_", s).strip("_")
    # Strip leading digits
    s = re.sub(r"^\d+", "", s)
    return s or name.lower()


class SaildroneProvider(ProviderBase):
    name = "saildrone"
    # TODO: Add 'echodata' and 'adcp' once processing logic is implemented
    supported_modules: list[ProcessingModule] = ["geotrack"]
    
    # Semantic mappings: Saildrone-specific column names → canonical field names
    # This allows cross-provider interoperability
    SEMANTIC_MAPPINGS = {
        # Core navigation
        "SOG": "speed_over_ground_ms",
        "SOG_FILTERED_MEAN": "speed_over_ground_filtered_mean_ms",
        "SOG_FILTERED_STDDEV": "speed_over_ground_filtered_stddev_ms",
        "SOG_FILTERED_MAX": "speed_over_ground_filtered_max_ms",
        "SOG_FILTERED_MIN": "speed_over_ground_filtered_min_ms",
        "COG": "course_over_ground_deg",
        "COG_FILTERED_MEAN": "course_over_ground_filtered_mean_deg",
        "COG_FILTERED_STDDEV": "course_over_ground_filtered_stddev_deg",
        "HDG": "heading_deg",
        "HDG_FILTERED_MEAN": "heading_filtered_mean_deg",
        "HDG_FILTERED_STDDEV": "heading_filtered_stddev_deg",
        
        # Platform orientation
        "ROLL_FILTERED_MEAN": "roll_filtered_mean_deg",
        "ROLL_FILTERED_STDDEV": "roll_filtered_stddev_deg",
        "ROLL_FILTERED_PEAK": "roll_filtered_peak_deg",
        "PITCH_FILTERED_MEAN": "pitch_filtered_mean_deg",
        "PITCH_FILTERED_STDDEV": "pitch_filtered_stddev_deg",
        "PITCH_FILTERED_PEAK": "pitch_filtered_peak_deg",
        
        # Wing orientation
        "HDG_WING": "wing_heading_deg",
        "WING_HDG_FILTERED_MEAN": "wing_heading_filtered_mean_deg",
        "WING_HDG_FILTERED_STDDEV": "wing_heading_filtered_stddev_deg",
        "WING_ROLL_FILTERED_MEAN": "wing_roll_filtered_mean_deg",
        "WING_ROLL_FILTERED_STDDEV": "wing_roll_filtered_stddev_deg",
        "WING_ROLL_FILTERED_PEAK": "wing_roll_filtered_peak_deg",
        "WING_PITCH_FILTERED_MEAN": "wing_pitch_filtered_mean_deg",
        "WING_PITCH_FILTERED_STDDEV": "wing_pitch_filtered_stddev_deg",
        "WING_PITCH_FILTERED_PEAK": "wing_pitch_filtered_peak_deg",
        "WING_ANGLE": "wing_angle_deg",
        
        # Meteorological
        "WIND_FROM_MEAN": "wind_direction_mean_deg",
        "WIND_FROM_STDDEV": "wind_direction_stddev_deg",
        "WIND_SPEED_MEAN": "wind_speed_mean_ms",
        "WIND_SPEED_STDDEV": "wind_speed_stddev_ms",
        "UWND_MEAN": "wind_u_component_mean_ms",
        "UWND_STDDEV": "wind_u_component_stddev_ms",
        "VWND_MEAN": "wind_v_component_mean_ms",
        "VWND_STDDEV": "wind_v_component_stddev_ms",
        "WWND_MEAN": "wind_w_component_mean_ms",
        "WWND_STDDEV": "wind_w_component_stddev_ms",
        "GUST_WND_MEAN": "wind_gust_mean_ms",
        "GUST_WND_STDDEV": "wind_gust_stddev_ms",
        "WIND_MEASUREMENT_HEIGHT_MEAN": "wind_measurement_height_mean_m",
        "WIND_MEASUREMENT_HEIGHT_STDDEV": "wind_measurement_height_stddev_m",
        "TEMP_AIR_MEAN": "air_temperature_mean_c",
        "TEMP_AIR_STDDEV": "air_temperature_stddev_c",
        "RH_MEAN": "relative_humidity_mean_percent",
        "RH_STDDEV": "relative_humidity_stddev_percent",
        "BARO_PRES_MEAN": "barometric_pressure_mean_hpa",
        "BARO_PRES_STDDEV": "barometric_pressure_stddev_hpa",
        
        # Radiation
        "PAR_AIR_MEAN": "par_air_mean_umol_s_m2",
        "PAR_AIR_STDDEV": "par_air_stddev_umol_s_m2",
        "SW_IRRAD_TOTAL_MEAN": "shortwave_irradiance_total_mean_w_m2",
        "SW_IRRAD_TOTAL_STDDEV": "shortwave_irradiance_total_stddev_w_m2",
        "SW_IRRAD_DIFFUSE_MEAN": "shortwave_irradiance_diffuse_mean_w_m2",
        "SW_IRRAD_DIFFUSE_STDDEV": "shortwave_irradiance_diffuse_stddev_w_m2",
        
        # Sea surface temperature
        "TEMP_IR_SEA_WING_UNCOMP_MEAN": "sea_surface_temperature_ir_uncompensated_mean_c",
        "TEMP_IR_SEA_WING_UNCOMP_STDDEV": "sea_surface_temperature_ir_uncompensated_stddev_c",
        
        # Waves
        "WAVE_DOMINANT_PERIOD": "wave_dominant_period_s",
        "WAVE_SIGNIFICANT_HEIGHT": "wave_significant_height_m",
        
        # Water column (near-surface)
        "TEMP_DEPTH_HALFMETER_MEAN": "water_temperature_0_5m_mean_c",
        "TEMP_DEPTH_HALFMETER_STDDEV": "water_temperature_0_5m_stddev_c",
        
        # CTD (SBE37)
        "TEMP_SBE37_MEAN": "water_temperature_ctd_mean_c",
        "TEMP_SBE37_STDDEV": "water_temperature_ctd_stddev_c",
        "SAL_SBE37_MEAN": "salinity_ctd_mean_psu",
        "SAL_SBE37_STDDEV": "salinity_ctd_stddev_psu",
        "COND_SBE37_MEAN": "conductivity_ctd_mean_ms_cm",
        "COND_SBE37_STDDEV": "conductivity_ctd_stddev_ms_cm",
        
        # Dissolved oxygen
        "O2_CONC_SBE37_MEAN": "oxygen_concentration_mean_umol_l",
        "O2_CONC_SBE37_STDDEV": "oxygen_concentration_stddev_umol_l",
        "O2_SAT_SBE37_MEAN": "oxygen_saturation_mean_percent",
        "O2_SAT_SBE37_STDDEV": "oxygen_saturation_stddev_percent",
        
        # Chlorophyll
        "CHLOR_WETLABS_MEAN": "chlorophyll_fluorescence_mean_ug_l",
        "CHLOR_WETLABS_STDDEV": "chlorophyll_fluorescence_stddev_ug_l",
    }

    def identify_platform(self, filename: str) -> str | None:
        """Extract platform/campaign ID from Saildrone filename.
        
        Saildrone filename format: sd{drone_id}_{mission}_{year}_{hash}_{hash}_{hash}.csv
        Returns: sd{drone_id}_{mission}_{year} (matches ERDDAP Dataset ID pattern)
        
        This ensures campaign_id aligns with NOAA PMEL ERDDAP server conventions.
        Example: sd1030_tpos_2023_7ef2_e8f7_98f9.csv → sd1030_tpos_2023
        
        Args:
            filename: Name of the Saildrone CSV file
            
        Returns:
            Platform ID in format sd{id}_{mission}_{year}, or None if not parsable
        """
        # Remove file extension
        name_without_ext = filename.rsplit('.', 1)[0]
        parts = name_without_ext.split("_")
        
        # Saildrone format: sd{id}_{mission}_{year}_{hash}_{hash}_{hash}
        # We want: sd{id}_{mission}_{year}
        if len(parts) >= 3 and parts[0].startswith('sd') and parts[0][2:].isdigit():
            # Check if third part looks like a year (4 digits)
            if len(parts[2]) == 4 and parts[2].isdigit():
                return f"{parts[0]}_{parts[1]}_{parts[2]}"
        
        # Fallback: return first part (drone ID only) for older formats
        return parts[0] if parts else None

    def enrich_dataframe(self, df: pd.DataFrame, metadata: dict | None = None) -> pd.DataFrame:
        """
        Enrich the Saildrone dataframe.
        
        Args:
            df: Input dataframe
            metadata: Optional metadata dict (not used by Saildrone provider)
            
        Returns:
            Enriched dataframe
        """
        out = df.copy()
        # Ensure platform_id exists; fall back to string type
        if "platform_id" in out.columns:
            out["platform_id"] = out["platform_id"].astype(str)
        # Ensure time column to ISO if present
        if "time" in out.columns and not pd.api.types.is_datetime64_any_dtype(out["time"]):
            with pd.option_context("future.no_silent_downcasting", True):
                try:
                    out["time"] = pd.to_datetime(out["time"], errors="coerce", utc=True)
                except Exception:
                    pass
        return out

    def units_mapping(self, header: Iterable[str], units_row: Iterable[str] | None = None, 
                     metadata: dict | None = None) -> dict[str, Any]:
        """
        Extract units mapping for Saildrone data.
        
        Args:
            header: Column names
            units_row: Optional units row from CSV
            metadata: Optional metadata dict (not used by Saildrone provider)
            
        Returns:
            Dictionary mapping column names to units
        """
        mapping: dict[str, Any] = {}
        if units_row is not None:
            for col, unit in zip(header, units_row):
                u = (unit or "").strip()
                mapping[col] = u if u else None
        # If units_row not provided, return empty (caller may compute heuristics elsewhere)
        return mapping

    def alias_mapping(self, columns: Iterable[str]) -> dict[str, str]:
        """
        Generate alias mapping with semantic + syntactic normalization.
        
        Priority:
        1. Semantic mappings (provider-specific → canonical vocabulary)
        2. Syntactic normalization (snake_case conversion)
        
        This enables cross-provider interoperability while maintaining backward compatibility.
        """
        aliases: dict[str, str] = {}
        for col in columns:
            # First try semantic mapping (provider-specific → canonical)
            if col in self.SEMANTIC_MAPPINGS:
                aliases[col] = self.SEMANTIC_MAPPINGS[col]
            else:
                # Fall back to syntactic normalization
                normalized = _normalize_alias(col)
                if normalized != col:
                    aliases[col] = normalized
        return aliases

    def parquet_metadata(self, df: pd.DataFrame, metadata: dict | None = None) -> dict[str, Any]:
        """
        Generate parquet metadata for Saildrone data.
        
        Args:
            df: Dataframe to generate metadata for
            metadata: Optional metadata dict (not used by Saildrone provider)
            
        Returns:
            Dictionary of metadata key-value pairs
        """
        # Minimal provider tag; can expand later with roles/stats
        return {
            "oceanstream:provider": {
                "name": self.name,
                "columns": list(df.columns),
            }
        }
    
    def supports_module(self, module: ProcessingModule) -> bool:
        """Check if this provider supports the given processing module."""
        return module in self.supported_modules

    # Optional: helper to convert a row into the generic model
    def to_models(self, df: pd.DataFrame) -> list[OceanographicMeasurement]:
        models: list[OceanographicMeasurement] = []
        for _, row in df.iterrows():
            ts = row.get("time")
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts is not None else "")
            models.append(
                OceanographicMeasurement(
                    platform_id=str(row.get("platform_id", "")),
                    latitude=float(row.get("latitude")),
                    longitude=float(row.get("longitude")),
                    timestamp=ts_str,
                    temperature=float(row["temperature"]) if "temperature" in row and pd.notna(row["temperature"]) else None,
                    salinity=float(row["salinity"]) if "salinity" in row and pd.notna(row["salinity"]) else None,
                    depth=float(row["depth"]) if "depth" in row and pd.notna(row["depth"]) else None,
                    other_measurements=None,
                )
            )
        return models
