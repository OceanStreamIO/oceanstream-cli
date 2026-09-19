"""Acquire independent offshore-depth screening and acquisition-time gauge data."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.windows import from_bounds

ETOPO_URL = (
    "https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/15s/"
    "15s_surface_elev_gtif/ETOPO_2022_v1_15s_N30W090_surface.tif"
)
TIDE_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
WHEN = datetime.fromisoformat("2017-02-08T16:16:20.341857+00:00")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(output: Path) -> None:
    if (output / "manifest.json").exists():
        raise FileExistsError("Inputs already acquired; use a new directory for a new request.")
    output.mkdir(parents=True, exist_ok=True)
    etopo = output / "ETOPO_2022_v1_15s_N30W090_surface.tif"
    if not etopo.exists():
        response = requests.get(ETOPO_URL, stream=True, timeout=(30, 180))
        response.raise_for_status()
        partial = etopo.with_suffix(".part")
        with partial.open("wb") as stream:
            for chunk in response.iter_content(1024 * 1024):
                stream.write(chunk)
        partial.replace(etopo)
    with rasterio.open(etopo) as src:
        window = from_bounds(-81.80, 24.33, -81.69, 24.73, src.transform)
        window = window.round_offsets().round_lengths()
        values = src.read(1, window=window)
        profile = src.profile.copy()
        profile.update(
            width=window.width, height=window.height, transform=src.window_transform(window)
        )
        subset = output / "offshore_elevation_etopo.tif"
        with rasterio.open(subset, "w", **profile) as dst:
            dst.write(values, 1)
            dst.update_tags(
                source=ETOPO_URL,
                purpose="offshore screening only; never shallow retrieval depth",
                vertical_datum="EGM2008 geoid; not MLLW",
            )
        depth_by_lat = {}
        for lat in (24.35, 24.38, 24.40, 24.42, 24.45, 24.50):
            samples = list(src.sample([(lon, lat) for lon in (-81.78, -81.75, -81.71)]))
            depth_by_lat[str(lat)] = [-float(v[0]) for v in samples]
    parameters = {
        "product": "water_level",
        "station": "8724580",
        "datum": "MLLW",
        "begin_date": "20170208 16:00",
        "end_date": "20170208 16:30",
        "units": "metric",
        "time_zone": "gmt",
        "format": "json",
        "application": "oceanstream_key_west_optical_diagnostic",
    }
    response = requests.get(TIDE_URL, params=parameters, timeout=60)
    response.raise_for_status()
    (output / "tide-response.json").write_text(response.text)
    payload = response.json()
    observations = payload.get("data", [])
    if len(observations) < 2:
        raise ValueError(f"No bracketing NOAA water-level observations: {payload}")
    times = np.array(
        [
            datetime.strptime(v["t"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp()
            for v in observations
        ]
    )
    if not times.min() <= WHEN.timestamp() <= times.max():
        raise ValueError("Gauge observations do not bracket the tile sensing time.")
    heights = np.array([float(v["v"]) for v in observations])
    if not np.isfinite(heights).all() or any(v.get("q") != "v" for v in observations):
        raise ValueError("Gauge series is nonfinite or not verified.")
    tide = {
        "station": "8724580",
        "station_metadata": payload.get("metadata"),
        "request_url": response.url,
        "parameters": parameters,
        "when": WHEN.isoformat(),
        "offset_m": float(np.interp(WHEN.timestamp(), times, heights)),
        "vertical_datum": "MLLW",
        "derivation": "independent_verified_gauge",
        "interpolation": "linear between bracketing 6-minute observations",
        "spatial_uncertainty_m": None,
        "caveat": "A single Key West gauge does not resolve water-level gradients along the strip.",
        "sensitivity_depth_offsets_m": [-0.25, 0.25],
        "sensitivity_definition": "Prespecified scenarios, not confidence bounds.",
    }
    (output / "tide.json").write_text(json.dumps(tide, indent=2) + "\n")
    manifest = {
        "schema_version": "1.0",
        "completed_at": datetime.now(UTC).isoformat(),
        "etopo_url": ETOPO_URL,
        "depth_by_latitude_m": depth_by_lat,
        "etopo_scope": "Independent coarse depth used only for conservative offshore screening.",
        "tide": tide,
        "files": {p.name: digest(p) for p in output.iterdir() if p.is_file()},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    fetch(parser.parse_args().output)
