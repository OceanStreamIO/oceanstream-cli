"""Acquire only the geographically preregistered fresh Key West reference strips."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from key_west_vdatum import apply
from key_west_vdatum import prepare as prepare_datum
from prepare_key_west_lidar import prepare as prepare_lidar


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registration", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.registration.read_text())
    for name, bounds in spec["fresh_evaluation_bounds"].items():
        lidar = args.output / name / "lidar"
        datum = args.output / name / "datum"
        if not (lidar / "lidar_preparation.json").exists():
            prepare_lidar(lidar, tuple(bounds))
        prepare_datum("geoid12b", datum, tuple(bounds), tuple(spec["datum_grid_shape"]))
        corrected = lidar / "elevation_mllw_10m.tif"
        if not corrected.exists():
            apply(lidar / "elevation_navd88_10m.tif", "geoid12b", corrected, datum)
        print(f"Completed reference preparation: {name}", flush=True)


if __name__ == "__main__":
    main()
