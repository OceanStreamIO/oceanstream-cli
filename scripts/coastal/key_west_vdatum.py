"""Reconstruct the Key West reference datum with cached NOAA VDatum requests.

The grid is deliberately small and fixed before optical retrieval. Its centre is
held out to check interpolation. This is a spatial datum conversion, not the
water level at Sentinel-2 acquisition. Input rasters must contain NAVD88 elevation
(positive upward), and the caller must identify the source geoid from metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from scipy.interpolate import LinearNDInterpolator, RegularGridInterpolator

API = "https://vdatum.noaa.gov/vdatumweb/api/convert"
DOCS = "https://www.vdatum.noaa.gov/docs/services.html"
BOUNDS = (423000.0, 2721000.0, 426000.0, 2724000.0)
CACHE = Path(".cache/coastal/reference/key-west-2017/datum")
GRID_CRS = "EPSG:6346"


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def request_point(x: float, y: float, geoid: str, cache: Path) -> dict:
    """Cache request provenance separately from the untouched response bytes."""
    transformer = Transformer.from_crs(GRID_CRS, "EPSG:6318", always_xy=True)
    longitude, latitude = transformer.transform(x, y)
    parameters = {
        "s_x": f"{longitude:.10f}",
        "s_y": f"{latitude:.10f}",
        "s_z": "0",
        "s_h_frame": "NAD83_2011",
        "t_h_frame": "NAD83_2011",
        "s_v_frame": "NAVD88",
        "t_v_frame": "MLLW",
        "s_v_geoid": geoid,
        "t_v_geoid": geoid,
        "s_coor": "geo",
        "t_coor": "geo",
        "s_v_unit": "m",
        "t_v_unit": "m",
        "s_v_elevation": "height",
        "t_v_elevation": "height",
    }
    url = API + "?" + urllib.parse.urlencode(parameters)
    identity = hashlib.sha256(url.encode()).hexdigest()
    response_path = cache / f"{identity}.response.json"
    request_path = cache / f"{identity}.request.json"
    if not response_path.exists() or not request_path.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "oceanstream-key-west/1"})
        requested_at = datetime.now(UTC).isoformat()
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
        # Decode before caching so an HTTP success containing HTML cannot be reused.
        json.loads(raw)
        response_path.write_bytes(raw)
        write_json(
            request_path,
            {
                "requested_at_utc": requested_at,
                "url": url,
                "parameters": parameters,
                "response_sha256": sha256(response_path),
                "horizontal_transform": transformer.description,
                "grid_x": x,
                "grid_y": y,
                "grid_crs": GRID_CRS,
            },
        )
    provenance = json.loads(request_path.read_text())
    if provenance["url"] != url or provenance["response_sha256"] != sha256(response_path):
        raise ValueError(f"Cached VDatum request or checksum changed: {request_path}")
    result = json.loads(response_path.read_text())
    for key, expected in {
        "s_v_frame": "NAVD88",
        "t_v_frame": "MLLW",
        "s_h_frame": "NAD83_2011",
        "t_h_frame": "NAD83_2011",
        "s_v_geoid": geoid,
        "t_v_geoid": geoid,
        "s_v_unit": "m",
        "t_v_unit": "m",
        "s_v_elevation": "height",
        "t_v_elevation": "height",
    }.items():
        if result.get(key) != expected:
            raise ValueError(f"VDatum response {key} != {expected}: {result}")
    offset = float(result["t_z"])
    if offset == -999999 and not result.get("uncertainty"):
        return {
            "x": x,
            "y": y,
            "longitude_nad83_2011": longitude,
            "latitude_nad83_2011": latitude,
            "offset_m": None,
            "api_uncertainty_m": None,
            "status": "outside_vdatum_tidal_domain",
            "request": request_path.name,
            "response": response_path.name,
            "response_sha256": sha256(response_path),
        }
    uncertainty = float(result["uncertainty"])
    if not math.isfinite(offset) or abs(offset) > 5:
        raise ValueError(f"Invalid Key West datum offset: {result}")
    if not math.isfinite(uncertainty) or not 0 <= uncertainty <= 1:
        raise ValueError(f"Invalid datum uncertainty: {result}")
    return {
        "x": x,
        "y": y,
        "longitude_nad83_2011": longitude,
        "latitude_nad83_2011": latitude,
        "offset_m": offset,
        "api_uncertainty_m": uncertainty,
        "status": "valid",
        "request": request_path.name,
        "response": response_path.name,
        "response_sha256": sha256(response_path),
    }


def interpolate(surface: dict, key: str, points: np.ndarray) -> np.ndarray:
    """Use bilinear interpolation, or triangulated valid nodes if a node is on land."""
    values = np.array(surface[key], dtype="float64")
    if np.isfinite(values).all():
        return RegularGridInterpolator(
            (surface["y"], surface["x"]), values, bounds_error=False, fill_value=np.nan
        )(points)
    xs, ys = np.meshgrid(surface["x"], surface["y"])
    nodes = np.column_stack([ys.ravel(), xs.ravel()])
    valid = np.isfinite(values.ravel())
    return LinearNDInterpolator(nodes[valid], values.ravel()[valid], fill_value=np.nan)(points)


def prepare(
    geoid: str,
    cache: Path,
    bounds: tuple[float, float, float, float] = BOUNDS,
    grid_shape: tuple[int, int] = (4, 4),
) -> dict:
    """Freeze a node surface and an independent centre interpolation check."""
    rows, columns = grid_shape
    if rows < 2 or columns < 2:
        raise ValueError("A datum grid needs at least two rows and columns")
    if rows % 2 and columns % 2:
        raise ValueError("At least one even grid dimension is needed for a held-out centre")
    if not all(math.isfinite(value) for value in bounds):
        raise ValueError("Bounds must be finite")
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError("Bounds must be west, south, east, north")
    cache.mkdir(parents=True, exist_ok=True)
    surface_path = cache / f"surface_{geoid}.json"
    if surface_path.exists():
        previous = json.loads(surface_path.read_text())
        previous_shape = [len(previous["y"]), len(previous["x"])]
        if previous["bounds"] != list(bounds) or previous_shape != list(grid_shape):
            raise ValueError("Use a new cache directory when changing a frozen datum grid")
        for node in [*previous["nodes"], previous["held_out_centre"]]:
            if sha256(cache / node["response"]) != node["response_sha256"]:
                raise ValueError("Datum control response checksum changed")
        return previous
    xs = np.linspace(bounds[0], bounds[2], columns)
    ys = np.linspace(bounds[1], bounds[3], rows)
    nodes = []
    for y in ys:
        for x in xs:
            node = request_point(float(x), float(y), geoid, cache)
            nodes.append(node)
            print(f"VDatum {len(nodes)}/{rows * columns}: {node['offset_m']} m", flush=True)
    offsets = np.array([node["offset_m"] for node in nodes], dtype="float64").reshape(rows, columns)
    uncertainties = np.array(
        [node["api_uncertainty_m"] for node in nodes], dtype="float64"
    ).reshape(rows, columns)
    centre = request_point(float(xs.mean()), float(ys.mean()), geoid, cache)
    if centre["offset_m"] is None:
        raise ValueError("Held-out centre is outside the VDatum tidal domain")
    interpolation_surface = {"x": xs, "y": ys, "offset_m": offsets}
    interpolated = float(
        interpolate(interpolation_surface, "offset_m", np.array([[ys.mean(), xs.mean()]]))[0]
    )
    if not math.isfinite(interpolated):
        raise ValueError("Insufficient valid datum controls to interpolate held-out centre")
    centre["interpolated_offset_m"] = interpolated
    centre["absolute_interpolation_error_m"] = abs(interpolated - centre["offset_m"])
    maximum_error = 0.03
    if centre["absolute_interpolation_error_m"] > maximum_error:
        raise ValueError(f"Held-out datum interpolation error exceeds {maximum_error} m: {centre}")
    surface = {
        "schema_version": "1.0.0",
        "source_vertical_datum": "NAVD88",
        "source_geoid": geoid,
        "target_vertical_datum": "MLLW",
        "target_geoid": geoid,
        "equation": "elevation_mllw = elevation_navd88 + offset_m",
        "depth_equation": "depth_mllw = -elevation_mllw",
        "grid_crs": GRID_CRS,
        "bounds": list(bounds),
        "grid_shape": list(grid_shape),
        "x": xs.tolist(),
        "y": ys.tolist(),
        "offset_m": [
            [float(value) if np.isfinite(value) else None for value in row] for row in offsets
        ],
        "api_uncertainty_m": [
            [float(value) if np.isfinite(value) else None for value in row] for row in uncertainties
        ],
        "nodes": nodes,
        "held_out_centre": centre,
        "maximum_centre_interpolation_error_m": maximum_error,
        "interpolation": (
            "bilinear in NAD83(2011) UTM zone17 coordinates; no extrapolation"
            if np.isfinite(offsets).all()
            else "piecewise linear triangulation of valid datum nodes in NAD83(2011) UTM zone17; "
            "no extrapolation beyond their convex hull"
        ),
        "valid_control_nodes": int(np.isfinite(offsets).sum()),
        "invalid_control_nodes": int((~np.isfinite(offsets)).sum()),
        "api": API,
        "documentation": DOCS,
        "prepared_at_utc": datetime.now(UTC).isoformat(),
        "limitations": [
            "The held-out centre checks interpolation locally; it is not a global error bound.",
            "API uncertainty is retained as returned; no confidence level is inferred.",
            "Datum conversion does not estimate acquisition-time water level.",
        ],
    }
    write_json(surface_path, surface)
    return surface


def apply(source: Path, geoid: str, output: Path, cache: Path) -> dict:
    surface_path = cache / f"surface_{geoid}.json"
    if not surface_path.exists():
        raise FileNotFoundError(f"Run prepare with the preregistered bounds first: {surface_path}")
    surface = json.loads(surface_path.read_text())
    if surface["source_geoid"] != geoid:
        raise ValueError("Source geoid does not match datum surface")
    for node in [*surface["nodes"], surface["held_out_centre"]]:
        if sha256(cache / node["response"]) != node["response_sha256"]:
            raise ValueError("Datum control response checksum changed")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Choose a new output path: {output}")
    with rasterio.open(source) as dataset:
        if CRS.from_user_input(dataset.crs) != CRS.from_user_input(GRID_CRS):
            raise ValueError(f"Expected {GRID_CRS}, got {dataset.crs}; reproject explicitly first")
        source_tags = dataset.tags()
        source_datum = source_tags.get("vertical_datum", "").upper()
        if source_datum and source_datum != "NAVD88":
            raise ValueError(f"Source tagged {source_datum}, expected NAVD88")
        for geoid_key in ("geoid", "geoid_model"):
            source_geoid = source_tags.get(geoid_key, "").lower()
            if source_geoid and source_geoid != geoid:
                raise ValueError(f"Source tagged {source_geoid}, caller provided {geoid}")
        elevation = dataset.read(1, masked=True).filled(np.nan).astype("float64")
        profile = dataset.profile.copy()
        rr, cc = np.indices(elevation.shape, dtype="float64")
        xs, ys = dataset.transform * (cc + 0.5, rr + 0.5)
        points = np.column_stack([ys.ravel(), xs.ravel()])
        offsets = interpolate(surface, "offset_m", points).reshape(elevation.shape)
        uncertainty = interpolate(surface, "api_uncertainty_m", points).reshape(elevation.shape)
    source_valid = np.isfinite(elevation)
    valid = source_valid & np.isfinite(offsets) & np.isfinite(uncertainty)
    if not valid.any():
        raise ValueError("No valid source elevation pixels")
    corrected = np.where(valid, elevation + offsets, np.nan).astype("float32")
    offsets = np.where(valid, offsets, np.nan).astype("float32")
    uncertainty = np.where(valid, uncertainty, np.nan).astype("float32")
    profile.update(dtype="float32", nodata=np.nan, count=1, compress="deflate")
    offset_path = output.with_name(output.stem + "_datum_offset.tif")
    uncertainty_path = output.with_name(output.stem + "_datum_uncertainty.tif")
    for path in (offset_path, uncertainty_path):
        if path.exists():
            raise FileExistsError(f"Choose new output paths: {path}")
    for path, data, name in (
        (output, corrected, "elevation_mllw"),
        (offset_path, offsets, "navd88_to_mllw_elevation_offset"),
        (uncertainty_path, uncertainty, "vdatum_api_uncertainty"),
    ):
        with rasterio.open(path, "w", **profile) as destination:
            destination.write(data, 1)
            destination.set_band_description(1, name)
            destination.update_tags(
                units="m",
                vertical_datum="MLLW" if path == output else "not_applicable",
                source_vertical_datum="NAVD88",
                source_geoid=geoid,
                source_sha256=sha256(source),
                datum_surface_sha256=sha256(surface_path),
                height_sign="positive_up" if path == output else "not_applicable",
                scientific_status="reference_datum_reconstruction",
            )
    provenance = {
        "schema_version": "1.0.0",
        "source": str(source.resolve()),
        "source_sha256": sha256(source),
        "output": str(output.resolve()),
        "output_sha256": sha256(output),
        "offset": str(offset_path.resolve()),
        "offset_sha256": sha256(offset_path),
        "api_uncertainty": str(uncertainty_path.resolve()),
        "api_uncertainty_sha256": sha256(uncertainty_path),
        "datum_surface": str(surface_path.resolve()),
        "datum_surface_sha256": sha256(surface_path),
        "source_vertical_datum": "NAVD88",
        "source_geoid": geoid,
        "target_vertical_datum": "MLLW",
        "equation": surface["equation"],
        "source_tags": source_tags,
        "horizontal_transform": "none: source and output retain EPSG:6346 grid",
        "node_query_horizontal_transform": "EPSG:6346 to EPSG:6318 (NAD83(2011))",
        "valid_source_pixels": int(source_valid.sum()),
        "valid_output_pixels": int(np.isfinite(corrected).sum()),
        "source_pixels_outside_datum_control_support": int((source_valid & ~valid).sum()),
        "source_pixels_below_navd88": int((source_valid & (elevation < 0)).sum()),
        "source_pixels_below_navd88_without_datum_support": int(
            (source_valid & (elevation < 0) & ~valid).sum()
        ),
        "output_pixels_below_mllw": int((corrected < 0).sum()),
        "offset_range_m": [float(np.nanmin(offsets)), float(np.nanmax(offsets))],
        "api_uncertainty_range_m": [float(np.nanmin(uncertainty)), float(np.nanmax(uncertainty))],
        "held_out_centre": surface["held_out_centre"],
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "software": {"python": sys.version, "rasterio": rasterio.__version__},
        "script_sha256": sha256(Path(__file__)),
        "limitations": surface["limitations"],
    }
    write_json(output.with_suffix(".provenance.json"), provenance)
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "apply"))
    parser.add_argument("--source-geoid", required=True, choices=("geoid12b", "geoid18"))
    parser.add_argument("--cache-dir", type=Path, default=CACHE)
    parser.add_argument("--bounds", type=float, nargs=4, default=BOUNDS)
    parser.add_argument("--grid-shape", type=int, nargs=2, default=(4, 4), metavar=("ROWS", "COLS"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        result = prepare(
            arguments.source_geoid,
            arguments.cache_dir,
            tuple(arguments.bounds),
            tuple(arguments.grid_shape),
        )
    else:
        if arguments.source is None or arguments.output is None:
            parser.error("apply requires --source and --output")
        result = apply(
            arguments.source, arguments.source_geoid, arguments.output, arguments.cache_dir
        )
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
