"""
Generate interpolated measurement heatmaps as Cloud Optimized GeoTIFF (COG).

Uses scipy for interpolation and rasterio/GDAL for COG generation.
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

log = logging.getLogger(__name__)

# Common oceanographic variable units and color schemes
VARIABLE_METADATA = {
    'TEMP_SBE37_MEAN': {'unit': '°C', 'colormap': 'thermal', 'label': 'Sea Temperature (CTD)'},
    'TEMP_AIR_MEAN': {'unit': '°C', 'colormap': 'thermal', 'label': 'Air Temperature'},
    'SAL_SBE37_MEAN': {'unit': 'PSU', 'colormap': 'viridis', 'label': 'Salinity'},
    'CHLOR_WETLABS_MEAN': {'unit': 'µg/L', 'colormap': 'algae', 'label': 'Chlorophyll'},
    'BARO_PRES_MEAN': {'unit': 'hPa', 'colormap': 'coolwarm', 'label': 'Barometric Pressure'},
    'RH_MEAN': {'unit': '%', 'colormap': 'Blues', 'label': 'Relative Humidity'},
    'WIND_SPEED_MEAN': {'unit': 'm/s', 'colormap': 'wind', 'label': 'Wind Speed'},
}


def _extract_measurements(
    geoparquet_root: Path,
    variable: str,
    sample_rate: int = 1,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Extract measurement points from GeoParquet dataset.
    
    Args:
        geoparquet_root: Root directory of partitioned GeoParquet
        variable: Variable name to extract
        sample_rate: Take every Nth point (1 = all)
        
    Returns:
        Tuple of (points array [N, 2], values array [N])
    """
    # Find all parquet files
    parquet_files = list(geoparquet_root.rglob("*.parquet"))
    parquet_files = [f for f in parquet_files if f.name != 'metadata.parquet']
    
    if not parquet_files:
        raise ValueError(f"No parquet files found in {geoparquet_root}")
    
    all_points = []
    all_values = []
    
    for pf_path in parquet_files:
        try:
            table = pq.read_table(pf_path, columns=['longitude', 'latitude', variable])
            df = table.to_pandas()
            
            # Normalize column names
            df.columns = [c.lower() for c in df.columns]
            var_lower = variable.lower()
            
            if var_lower not in df.columns:
                continue
            
            # Drop NaN values
            df = df.dropna(subset=['longitude', 'latitude', var_lower])
            
            # Apply sampling
            if sample_rate > 1:
                df = df.iloc[::sample_rate]
            
            if len(df) > 0:
                all_points.append(df[['longitude', 'latitude']].values)
                all_values.append(df[var_lower].values)
                
        except Exception as e:
            log.warning(f"Failed to read {pf_path}: {e}")
            continue
    
    if not all_points:
        raise ValueError(f"No data found for variable {variable}")
    
    points = np.vstack(all_points)
    values = np.concatenate(all_values)
    
    log.info(f"Extracted {len(values)} points for {variable}")
    return points, values


def _mask_distant_cells(
    grid: np.ndarray,
    xi: np.ndarray,
    yi: np.ndarray,
    points: np.ndarray,
    max_dist: float,
    nodata: float,
) -> np.ndarray:
    """
    Set cells far from any data point to nodata.
    Uses KD-tree for efficient nearest neighbor search.
    """
    from scipy.spatial import cKDTree
    
    tree = cKDTree(points)
    grid_points = np.column_stack([xi.ravel(), yi.ravel()])
    distances, _ = tree.query(grid_points)
    
    mask = distances.reshape(grid.shape) > max_dist
    grid[mask] = nodata
    
    return grid


def _write_geotiff(
    output_path: Path,
    data: np.ndarray,
    min_lon: float,
    max_lon: float,
    min_lat: float,
    max_lat: float,
    nodata: float,
) -> None:
    """Write numpy array as GeoTIFF with proper georeferencing."""
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
    
    height, width = data.shape
    transform = from_bounds(min_lon, min_lat, max_lon, max_lat, width, height)
    
    # Flip vertically because rasterio expects top-to-bottom
    data_flipped = np.flipud(data)
    
    with rasterio.open(
        output_path,
        'w',
        driver='GTiff',
        height=height,
        width=width,
        count=1,
        dtype=data.dtype,
        crs=CRS.from_epsg(4326),
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(data_flipped, 1)


def _convert_to_cog(input_path: Path, output_path: Optional[Path] = None) -> Path:
    """
    Convert GeoTIFF to Cloud Optimized GeoTIFF using GDAL.
    
    Requires gdal_translate to be installed.
    """
    if output_path is None:
        output_path = input_path.with_suffix('.cog.tif')
    
    # Use gdal_translate with COG driver
    cmd = [
        'gdal_translate',
        '-of', 'COG',
        '-co', 'COMPRESS=DEFLATE',
        '-co', 'PREDICTOR=2',
        '-co', 'OVERVIEW_RESAMPLING=AVERAGE',
        str(input_path),
        str(output_path),
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        # Remove original non-COG file
        if input_path != output_path:
            input_path.unlink()
        return output_path
    except FileNotFoundError:
        log.warning("gdal_translate not found, keeping non-COG GeoTIFF")
        return input_path
    except subprocess.CalledProcessError as e:
        log.warning(f"COG conversion failed: {e.stderr.decode()}")
        return input_path


def generate_measurement_cog(
    geoparquet_root: Path,
    output_path: Path,
    variable: str,
    *,
    resolution_deg: float = 0.05,
    method: str = 'linear',
    search_radius_deg: float = 0.5,
    nodata: float = -9999.0,
    sample_rate: int = 1,
) -> Path:
    """
    Generate a COG heatmap for a measurement variable.
    
    Args:
        geoparquet_root: Root of partitioned GeoParquet dataset
        output_path: Output .tif path
        variable: Variable name from properties (e.g., 'TEMP_SBE37_MEAN')
        resolution_deg: Grid resolution in degrees (default 0.05 ≈ 5km)
        method: Interpolation method ('linear', 'nearest', 'cubic')
        search_radius_deg: Maximum distance from data points to interpolate
        nodata: NoData value for areas outside interpolation range
        sample_rate: Sample every Nth point (for large datasets)
        
    Returns:
        Path to generated COG file
    """
    from scipy.interpolate import griddata
    
    log.info(f"Generating heatmap for {variable}...")
    
    # 1. Read measurement points from GeoParquet
    points, values = _extract_measurements(geoparquet_root, variable, sample_rate)
    
    # 2. Determine bounding box with small buffer
    buffer = resolution_deg * 2
    min_lon, max_lon = points[:, 0].min() - buffer, points[:, 0].max() + buffer
    min_lat, max_lat = points[:, 1].min() - buffer, points[:, 1].max() + buffer
    
    # 3. Create grid
    x_steps = int((max_lon - min_lon) / resolution_deg)
    y_steps = int((max_lat - min_lat) / resolution_deg)
    
    log.info(f"Creating {x_steps}x{y_steps} grid ({x_steps * y_steps} cells)")
    
    xi = np.linspace(min_lon, max_lon, x_steps)
    yi = np.linspace(min_lat, max_lat, y_steps)
    xi_grid, yi_grid = np.meshgrid(xi, yi)
    
    # 4. Interpolate using scipy.griddata
    log.info(f"Interpolating with method={method}...")
    zi = griddata(points, values, (xi_grid, yi_grid), method=method, fill_value=nodata)
    
    # 5. Mask areas far from data points
    log.info(f"Masking cells > {search_radius_deg}° from data...")
    zi = _mask_distant_cells(zi, xi_grid, yi_grid, points, search_radius_deg, nodata)
    
    # Convert to float32 for smaller file size
    zi = zi.astype(np.float32)
    
    # 6. Write as GeoTIFF
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix('.tmp.tif')
    _write_geotiff(temp_path, zi, min_lon, max_lon, min_lat, max_lat, nodata)
    
    # 7. Convert to COG
    log.info("Converting to Cloud Optimized GeoTIFF...")
    final_path = _convert_to_cog(temp_path, output_path)
    
    log.info(f"Generated: {final_path} ({final_path.stat().st_size / 1024:.1f} KB)")
    return final_path


def discover_numeric_variables(geoparquet_root: Path) -> list[str]:
    """
    Discover numeric variables from a GeoParquet dataset.
    
    Returns variable names that appear to be measurements (uppercase, contain numbers).
    """
    parquet_files = list(geoparquet_root.rglob("*.parquet"))
    parquet_files = [f for f in parquet_files if f.name != 'metadata.parquet']
    
    if not parquet_files:
        return []
    
    # Read schema from first file
    pf = pq.ParquetFile(parquet_files[0])
    all_columns = set(pf.schema.names)
    
    # Filter to likely measurement columns
    system_cols = {'time', 'latitude', 'longitude', 'geometry', 'platform_id',
                   'campaign_id', 'trajectory', 'lat_bin', 'lon_bin'}
    
    numeric_vars = []
    for col in all_columns:
        col_lower = col.lower()
        if col_lower in system_cols:
            continue
        # Keep uppercase columns that end with _MEAN, _STDDEV, etc.
        if col.isupper() or col.endswith('_MEAN') or col.endswith('_mean'):
            numeric_vars.append(col)
    
    return sorted(numeric_vars)


def generate_all_heatmaps(
    geoparquet_root: Path,
    output_dir: Path,
    variables: Optional[list[str]] = None,
    *,
    resolution_deg: float = 0.05,
    method: str = 'linear',
    search_radius_deg: float = 0.5,
    sample_rate: int = 1,
    max_variables: int = 10,
) -> dict:
    """
    Generate COG heatmaps for all (or specified) variables.
    
    Args:
        geoparquet_root: Root of partitioned GeoParquet dataset
        output_dir: Directory to write COG files
        variables: Specific variables to process (None = auto-detect)
        resolution_deg: Grid resolution in degrees
        method: Interpolation method
        search_radius_deg: Max interpolation distance
        sample_rate: Sample every Nth point
        max_variables: Max number of variables to process
        
    Returns:
        Manifest dict with variable metadata
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if variables is None:
        variables = discover_numeric_variables(geoparquet_root)
        # Filter to common measurement variables
        priority_vars = ['TEMP_SBE37_MEAN', 'TEMP_AIR_MEAN', 'SAL_SBE37_MEAN',
                        'CHLOR_WETLABS_MEAN', 'BARO_PRES_MEAN', 'RH_MEAN', 'WIND_SPEED_MEAN']
        variables = [v for v in priority_vars if v in variables] + \
                    [v for v in variables if v not in priority_vars]
    
    variables = variables[:max_variables]
    log.info(f"Processing {len(variables)} variables: {variables}")
    
    manifest = {'variables': []}
    
    for var in variables:
        try:
            output_path = output_dir / f"{var.lower()}.tif"
            
            # Generate COG
            final_path = generate_measurement_cog(
                geoparquet_root,
                output_path,
                var,
                resolution_deg=resolution_deg,
                method=method,
                search_radius_deg=search_radius_deg,
                sample_rate=sample_rate,
            )
            
            # Read actual value range from generated file
            import rasterio
            with rasterio.open(final_path) as src:
                data = src.read(1)
                valid_data = data[data != -9999.0]
                if len(valid_data) > 0:
                    min_val = float(valid_data.min())
                    max_val = float(valid_data.max())
                else:
                    min_val = max_val = None
            
            # Get metadata
            meta = VARIABLE_METADATA.get(var, {'unit': '', 'colormap': 'viridis', 'label': var})
            
            manifest['variables'].append({
                'name': var,
                'file': final_path.name,
                'label': meta.get('label', var),
                'unit': meta.get('unit', ''),
                'colormap': meta.get('colormap', 'viridis'),
                'min': round(min_val, 3) if min_val is not None else None,
                'max': round(max_val, 3) if max_val is not None else None,
            })
            
        except Exception as e:
            log.error(f"Failed to generate heatmap for {var}: {e}")
            continue
    
    # Write manifest
    manifest_path = output_dir / 'manifest.json'
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    log.info(f"Wrote manifest: {manifest_path}")
    return manifest


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    if len(sys.argv) < 3:
        print("Usage: python heatmap.py <geoparquet_root> <output_dir> [variable]")
        sys.exit(1)
    
    geoparquet_root = Path(sys.argv[1])
    output_dir = Path(sys.argv[2])
    
    if len(sys.argv) > 3:
        # Single variable
        variable = sys.argv[3]
        generate_measurement_cog(geoparquet_root, output_dir / f"{variable.lower()}.tif", variable)
    else:
        # All variables
        generate_all_heatmaps(geoparquet_root, output_dir)
