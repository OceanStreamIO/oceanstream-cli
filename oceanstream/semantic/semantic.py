"""Semantic Mapping Scaffolding (MVP)

Transforms provider-specific column names into canonical OceanStream names and attaches
CF Standard Name + units metadata suitable for embedding into GeoParquet and STAC.

Design Goals:
- Non-invasive: operates on a DataFrame copy, returns enriched metadata but does not force renames unless requested.
- Pluggable data sources: CF table + alias table shipped as static JSON snapshots.
- Lightweight heuristics: name normalization + exact/alias/fuzzy match with confidence scoring.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
import json
import re
import pandas as pd

_NORMALIZE_NON_ALNUM = re.compile(r"[^0-9a-zA-Z]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])([A-Z])")
_MULTI_US = re.compile(r"_+")
_PREFIX_STRIP = ["sd_", "saildrone_", "raw_", "obs_"]


@dataclass
class SemanticConfig:
    enabled: bool = True
    cf_table_path: Optional[str] = None  # path to CF standard name snapshot (JSON)
    alias_table_path: Optional[str] = None  # provider/global alias map (JSON)
    min_confidence: float = 0.7
    rename_columns: bool = False  # if True apply canonical names to df copy


@dataclass
class SemanticResult:
    canonical_mapping: dict[str, str]  # original -> canonical
    cf_mapping: dict[str, dict[str, Any]]  # original -> {cf_standard_name, confidence}
    units: dict[str, str | None]  # canonical -> units (None if unknown)
    dataframe: pd.DataFrame  # possibly renamed copy


class SemanticMapper:
    def __init__(self, config: SemanticConfig):
        self.config = config
        self._cf_table: set[str] = set()
        self._aliases: dict[str, str] = {}
        if config.enabled:
            self._load_tables()

    def _get_builtin_table_path(self, filename: str) -> Path:
        """Get path to built-in semantic table."""
        return Path(__file__).parent / "data" / filename

    def _load_tables(self) -> None:
        # Load CF standard names table
        # Priority: 1. User-supplied path, 2. Built-in table
        cf_path = None
        if self.config.cf_table_path and Path(self.config.cf_table_path).exists():
            cf_path = Path(self.config.cf_table_path)
        else:
            builtin_cf = self._get_builtin_table_path("cf-standard-names.json")
            if builtin_cf.exists():
                cf_path = builtin_cf
        
        if cf_path:
            with open(cf_path, "r", encoding="utf-8") as f:
                cf_list = json.load(f)
            if isinstance(cf_list, list):
                self._cf_table = {str(x).lower() for x in cf_list}
            elif isinstance(cf_list, dict):
                self._cf_table = {str(k).lower() for k in cf_list.keys()}
        
        # Load alias table
        # Priority: 1. User-supplied path, 2. Built-in Saildrone aliases
        alias_path = None
        if self.config.alias_table_path and Path(self.config.alias_table_path).exists():
            alias_path = Path(self.config.alias_table_path)
        else:
            builtin_alias = self._get_builtin_table_path("saildrone-aliases.json")
            if builtin_alias.exists():
                alias_path = builtin_alias
        
        if alias_path:
            with open(alias_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                # assume mapping canonical->list[aliases] OR alias->canonical; normalize both
                for k, v in data.items():
                    if isinstance(v, list):
                        for alias in v:
                            self._aliases[str(alias).lower()] = str(k).lower()
                    else:
                        self._aliases[str(k).lower()] = str(v).lower()

    # --- Public API ---
    def apply(self, df: pd.DataFrame) -> SemanticResult:
        if not self.config.enabled:
            return SemanticResult({}, {}, {}, df.copy())
        profiled = self._profile_columns(df)
        canonical_map: dict[str, str] = {}
        cf_map: dict[str, dict[str, Any]] = {}
        units_map: dict[str, str | None] = {}

        for col, stats in profiled.items():
            if not stats["is_numeric"]:
                continue
            norm_name = self._normalize_name(col)
            canonical = self._explicit_alias(norm_name) or norm_name
            canonical_map[col] = canonical
            cf_name, conf = self._cf_match(canonical)
            if cf_name:
                cf_map[col] = {"cf_standard_name": cf_name, "confidence": conf}
            # units placeholder logic (MVP): try simple suffix heuristics
            units_map[canonical] = self._infer_units(col)

        out_df = df.copy()
        if self.config.rename_columns:
            rename_dict = {orig: canon for orig, canon in canonical_map.items() if canon != orig}
            if rename_dict:
                out_df = out_df.rename(columns=rename_dict)

        return SemanticResult(canonical_map, cf_map, units_map, out_df)

    # --- Helpers ---
    def _profile_columns(self, df: pd.DataFrame) -> dict[str, dict[str, Any]]:
        info: dict[str, dict[str, Any]] = {}
        for col in df.columns:
            series = df[col]
            non_null_ratio = series.notna().mean() if len(series) else 0
            is_numeric = pd.api.types.is_numeric_dtype(series)
            info[col] = {
                "non_null_ratio": non_null_ratio,
                "is_numeric": is_numeric,
            }
        return info

    def _normalize_name(self, name: str) -> str:
        s = name.strip()
        for p in _PREFIX_STRIP:
            if s.lower().startswith(p):
                s = s[len(p):]
        if s.isupper():
            s = s.lower()
        else:
            s = _CAMEL_BOUNDARY.sub(r"_\1", s).lower()
        s = _NORMALIZE_NON_ALNUM.sub("_", s)
        s = _MULTI_US.sub("_", s).strip("_")
        return s

    def _explicit_alias(self, norm_name: str) -> Optional[str]:
        return self._aliases.get(norm_name)

    def _cf_match(self, canonical: str) -> tuple[Optional[str], float]:
        # Exact
        if canonical in self._cf_table:
            return canonical, 1.0
        # Simple heuristic: replace common tokens and retry
        simplified = canonical.replace("sea_water_", "")
        for candidate in self._cf_table:
            if candidate.endswith(simplified) and len(simplified) > 4:
                return candidate, 0.85
        return None, 0.0

    def _infer_units(self, original_name: str) -> str | None:
        lname = original_name.lower()
        if any(k in lname for k in ("temp", "temperature")):
            return "degC"
        if "sal" in lname or "salinity" in lname:
            return "1e-3"
        if "speed" in lname:
            return "m s-1"
        if "depth" in lname:
            return "m"
        return None

# Future enhancement: function to emit parquet metadata dict from SemanticResult

def semantic_to_parquet_metadata(result: SemanticResult) -> dict[str, Any]:
    return {
        "oceanstream:aliases": {k: v for k, v in result.canonical_mapping.items() if k != v},
        "oceanstream:cf_standard_names": result.cf_mapping,
        "oceanstream:units": {k: v for k, v in result.units.items() if v is not None},
        "oceanstream:semantic_version": "sem-v0.1",
    }
