import json
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.fs as pafs
import pyarrow.parquet as pq

from .binning import suggest_lat_lon_bins_from_data


def _coerce_object_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Best-effort cleanup so Arrow doesn't choke on mixed object dtypes.

    - Strip whitespace from string values
    - Try numeric coercion when a majority of non-null values look numeric
    - Otherwise, cast to pandas string dtype for consistency
    """
    out = df.copy()
    for col in out.columns:
        s = out[col]
        # Keep key columns as-is (already validated upstream)
        if col in {"latitude", "longitude", "lat_bin", "lon_bin", "geometry"}:
            continue
        if pd.api.types.is_object_dtype(s):
            # Normalize whitespace-only strings to NA and trim
            s2 = s.map(lambda v: v.strip() if isinstance(v, str) else v)
            s2 = s2.replace(r"^$", pd.NA, regex=True)
            # Attempt numeric coercion
            numeric = pd.to_numeric(s2, errors="coerce")
            ratio = numeric.notna().mean() if len(s2) else 0.0
            if ratio >= 0.5:
                out[col] = numeric
            else:
                try:
                    out[col] = s2.astype("string")
                except Exception:
                    out[col] = s2.astype("string[python]")
    return out

def write_geoparquet(
    dataframe: pd.DataFrame,
    output_path: str | Path,
    lat_bins,
    lon_bins,
    *,
    units_metadata: dict[str, Any] | None = None,
    alias_mapping: dict[str, str] | None = None,
    geo_metadata: dict[str, Any] | None = None,
    provider_metadata: dict[str, Any] | None = None,
    semantic_metadata: dict[str, Any] | None = None,
    filesystem: Optional[pafs.FileSystem] = None,
):
    """Write the DataFrame to a GeoParquet dataset partitioned by lat/lon bins.
    
    Args:
        dataframe: DataFrame to write.
        output_path: Output path (local path or cloud path like container/prefix).
        lat_bins: Latitude bin edges.
        lon_bins: Longitude bin edges.
        units_metadata: Optional units metadata dict.
        alias_mapping: Optional column alias mapping.
        geo_metadata: Optional geo metadata.
        provider_metadata: Optional provider-specific metadata.
        semantic_metadata: Optional semantic metadata.
        filesystem: Optional PyArrow filesystem for cloud storage.
                   If None, writes to local filesystem.
    """
    output_dir = str(output_path)
    
    # Create directory if using local filesystem (or no filesystem specified)
    if filesystem is None:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
    elif isinstance(filesystem, pafs.LocalFileSystem):
        Path(output_dir).mkdir(parents=True, exist_ok=True)

    if lat_bins is None or lon_bins is None:
        s_lat_bins, s_lon_bins = suggest_lat_lon_bins_from_data(dataframe)
        lat_bins = lat_bins or s_lat_bins
        lon_bins = lon_bins or s_lon_bins

    dataframe = dataframe.copy()

    def _fmt_edge(x: float) -> str:
        s = f"{float(x):.6f}".rstrip('0').rstrip('.')
        return '0' if s in ('-0', '+0') else s

    lat_min, lat_max = float(lat_bins[0]), float(lat_bins[-1])
    lon_min, lon_max = float(lon_bins[0]), float(lon_bins[-1])
    lat_vals = dataframe['latitude'].clip(lat_min, lat_max)
    lon_vals = dataframe['longitude'].clip(lon_min, lon_max)

    lat_cat = pd.cut(lat_vals, bins=lat_bins, include_lowest=True, right=True)
    lon_cat = pd.cut(lon_vals, bins=lon_bins, include_lowest=True, right=True)

    lat_labels = [f"lat_{_fmt_edge(iv.left)}_{_fmt_edge(iv.right)}" for iv in lat_cat.cat.categories]
    lon_labels = [f"lon_{_fmt_edge(iv.left)}_{_fmt_edge(iv.right)}" for iv in lon_cat.cat.categories]

    dataframe['lat_bin'] = lat_cat.cat.rename_categories(lat_labels).astype(str)
    dataframe['lon_bin'] = lon_cat.cat.rename_categories(lon_labels).astype(str)

    # Final dtype cleanup to avoid ArrowInvalid on mixed object columns
    dataframe = _coerce_object_columns(dataframe)
    table = pa.Table.from_pandas(dataframe)
    meta = dict(table.schema.metadata or {})

    # Merge semantic metadata (if provided) with provider-supplied units/aliases.
    # Provider-supplied values take precedence over semantic where keys overlap.
    combined_units: dict[str, Any] | None = None
    combined_aliases: dict[str, Any] | None = None

    if semantic_metadata is not None:
        sem_units = semantic_metadata.get("oceanstream:units")
        sem_aliases = semantic_metadata.get("oceanstream:aliases")
        if sem_units or units_metadata:
            combined_units = {}
            if isinstance(sem_units, dict):
                combined_units.update(sem_units)
            if isinstance(units_metadata, dict):
                combined_units.update(units_metadata)
        if sem_aliases or alias_mapping:
            combined_aliases = {}
            if isinstance(sem_aliases, dict):
                combined_aliases.update(sem_aliases)
            if isinstance(alias_mapping, dict):
                combined_aliases.update(alias_mapping)

        # Write semantic-only blocks as well (cf_standard_names, semantic_version)
        if "oceanstream:cf_standard_names" in semantic_metadata:
            meta[b"oceanstream:cf_standard_names"] = json.dumps(
                semantic_metadata["oceanstream:cf_standard_names"]
            ).encode("utf-8")
        if "oceanstream:semantic_version" in semantic_metadata:
            meta[b"oceanstream:semantic_version"] = json.dumps(
                semantic_metadata["oceanstream:semantic_version"]
            ).encode("utf-8")

    # Fallback if no semantic provided: use provider values directly
    if combined_units is None and units_metadata is not None:
        combined_units = units_metadata
    if combined_aliases is None and alias_mapping is not None:
        combined_aliases = alias_mapping

    if combined_units is not None:
        meta[b"oceanstream:units"] = json.dumps(combined_units).encode("utf-8")
    if combined_aliases is not None:
        meta[b"oceanstream:aliases"] = json.dumps(combined_aliases).encode("utf-8")
    if provider_metadata is not None:
        # Keys are expected like "oceanstream:provider"; values are JSON-serializable
        for k, v in provider_metadata.items():
            try:
                meta[str(k).encode("utf-8")] = json.dumps(v).encode("utf-8")
            except Exception:
                # Fallback: store stringified value
                meta[str(k).encode("utf-8")] = str(v).encode("utf-8")

    geo_block: dict[str, Any] | None = None
    if geo_metadata is not None:
        geo_block = geo_metadata
    elif 'geometry' in table.schema.names:
        geo_block = {
            "version": "1.1.0",
            "primary_column": "geometry",
            "columns": {
                "geometry": {"encoding": "WKB", "geometry_type": "Point", "crs": "EPSG:4326"}
            },
        }

    if geo_block is not None:
        meta[b"geo"] = json.dumps(geo_block).encode("utf-8")

    if meta:
        table = table.replace_schema_metadata(meta)
    pq.write_to_dataset(
        table,
        root_path=output_dir,
        partition_cols=['lat_bin', 'lon_bin'],
        filesystem=filesystem,
    )
