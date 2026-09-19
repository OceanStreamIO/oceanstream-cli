#!/usr/bin/env python3
"""Freeze configured Key West experiment files before evaluating predictions."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def freeze(config_paths: list[Path], cache: Path, output: Path) -> None:
    if output.exists():
        raise FileExistsError("Input lock already exists; create a new registration.")
    output = output.resolve()
    source_root = Path(__file__).resolve().parents[2]
    paths = set((source_root / "oceanstream" / "coastal").rglob("*.py"))
    paths.update((source_root / "scripts" / "coastal").glob("*key_west*.py"))
    for config_path in config_paths:
        config_path = config_path.resolve()
        config = json.loads(config_path.read_text())
        base = config_path.parent
        if (base / config["input_lock"]).resolve() != output:
            raise ValueError("Every run configuration must point to this input lock.")
        paths.add(config_path)
        paths.add((base / config["reference_elevation_raster"]).resolve())
        paths.update((base / path).resolve() for path in config["additional_provenance_files"])
        acolite = (base / config["acolite_dir"]).resolve()
        paths.update(
            path for path in acolite.iterdir()
            if path.is_file() and path.suffix in {".tif", ".txt", ".json"}
        )
    for directory in ("lidar-transect", "datum-transect", "scene"):
        paths.update(
            path for path in (cache / directory).iterdir()
            if path.is_file() and path.suffix in {".tif", ".json", ".zip"}
        )
    files = {}
    for path in sorted({path.resolve() for path in paths}):
        if path == output:
            continue
        with path.open("rb") as stream:
            files[str(path)] = hashlib.file_digest(stream, "sha256").hexdigest()
    lock = {
        "schema_version": "1.0",
        "frozen_at": datetime.now(UTC).isoformat(),
        "purpose": "Key West SDB component experiment; frozen before first prediction evaluation",
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(json.dumps(lock, indent=2) + "\n")
    print(f"Frozen {len(files)} files: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configs", type=Path, nargs="+")
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze(args.configs, args.cache, args.output)


if __name__ == "__main__":
    main()
