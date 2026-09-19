#!/usr/bin/env python3
"""Run a preregistered Key West atmospheric correction in an isolated directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from oceanstream.coastal.acolite import output_is_complete, run_acolite, write_acolite_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--scene-manifest", type=Path, required=True)
    parser.add_argument("--acolite-path", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--variant", choices=["exp_published", "dsf_current"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    registration = json.loads(args.registration.read_text())
    scene = json.loads(args.scene_manifest.read_text())
    if scene["product_id"] != registration["scene_id"]:
        raise ValueError("Scene does not match the registered acquisition.")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=args.acolite_path, text=True
    ).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=args.acolite_path,
        text=True,
    ).strip()
    if commit != registration["acolite_commit"] or dirty:
        raise ValueError("ACOLITE checkout must match the registered clean commit.")
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError("Use a new empty atmospheric-correction output directory.")
    args.output.mkdir(parents=True, exist_ok=True)
    west, south, east, north = registration["processing_bbox_wgs84"]
    settings = write_acolite_settings(
        args.output,
        (south, west, north, east),
        Path(scene["safe_path"]),
        extra={
            **registration["acolite_common_settings"],
            **registration["acolite_variants"][args.variant],
        },
    )
    manifest = {
        "schema_version": "1.0",
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "variant": args.variant,
        "acolite_commit": commit,
        "registration_sha256": hashlib.sha256(args.registration.read_bytes()).hexdigest(),
        "scene_archive_sha256": scene["archive_sha256"],
        "settings_sha256": hashlib.sha256(settings.read_bytes()).hexdigest(),
    }
    (args.output / "run-start.json").write_text(json.dumps(manifest, indent=2) + "\n")
    try:
        run_acolite(settings, args.acolite_path, python_executable=args.python)
        if not output_is_complete(args.output):
            raise RuntimeError("ACOLITE exited without the required reflectance bands.")
        if not list(args.output.glob("*_L2W_Rrs_[0-9]*.tif")):
            raise RuntimeError("ACOLITE exited without the registered Rrs exports.")
    except Exception as error:
        manifest.update(
            status="execution_failed",
            failed_at=datetime.now(UTC).isoformat(),
            error=f"{type(error).__name__}: {error}",
        )
        (args.output / "failure.json").write_text(json.dumps(manifest, indent=2) + "\n")
        raise
    rasters = {}
    for path in sorted(args.output.glob("*.tif")):
        with path.open("rb") as stream:
            rasters[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest.update(status="completed", completed_at=datetime.now(UTC).isoformat(), rasters=rasters)
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Completed {args.variant}: {args.output}", flush=True)


if __name__ == "__main__":
    main()
