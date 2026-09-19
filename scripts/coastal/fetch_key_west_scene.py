#!/usr/bin/env python3
"""Fetch the preregistered Key West reference acquisition, preserving provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import requests

from oceanstream.coastal.acquire.cdse import (
    CDSECredentials,
    SceneCandidate,
    download_scene,
)

PRODUCT_ID = "14bf0974-868d-429d-b506-a9cbe6ada92f"
PRODUCT_NAME = "S2A_MSIL1C_20170208T160411_N0500_R097_T17RMH_20231024T232642.SAFE"
CATALOG_URL = (
    f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products({PRODUCT_ID})"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if (args.output / "scene-manifest.json").exists():
        raise FileExistsError("Scene already completed; use its manifest or a new directory.")
    credentials_env = dict(os.environ)
    if args.env_file:
        from dotenv import dotenv_values

        credentials_env.update(
            {k: v for k, v in dotenv_values(args.env_file).items() if k.startswith("CDSE_") and v}
        )
    credentials = CDSECredentials.from_env(credentials_env)
    response = requests.get(CATALOG_URL, timeout=60)
    response.raise_for_status()
    metadata = response.json()
    if metadata["Id"] != PRODUCT_ID or metadata["Name"] != PRODUCT_NAME:
        raise ValueError("Catalogue identity differs from preregistered scene.")
    candidate = SceneCandidate(
        product_id=PRODUCT_ID,
        name=PRODUCT_NAME,
        sensing_datetime=datetime.fromisoformat(metadata["ContentDate"]["Start"]),
        cloud_cover_pct=float("nan"),
        size_bytes=metadata["ContentLength"],
        online=metadata["Online"],
        tile="T17RMH",
        platform="S2A",
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "catalogue.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Downloading {PRODUCT_NAME}", flush=True)
    archive = download_scene(candidate, args.output, credentials=credentials)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("SAFE archive failed CRC validation.")
        for member in bundle.infolist():
            target = (args.output / member.filename).resolve()
            if not target.is_relative_to(args.output.resolve()):
                raise ValueError("Unsafe archive member path.")
        bundle.extractall(args.output)
    safe_path = args.output / PRODUCT_NAME
    if not (safe_path / "MTD_MSIL1C.xml").is_file():
        raise ValueError("Extracted SAFE lacks product metadata.")
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest = {
        "schema_version": "1.0",
        "completed_at": datetime.now(UTC).isoformat(),
        "catalogue_url": CATALOG_URL,
        "product_id": PRODUCT_ID,
        "product_name": PRODUCT_NAME,
        "archive": str(archive.resolve()),
        "archive_sha256": checksum,
        "archive_bytes": archive.stat().st_size,
        "safe_path": str(safe_path.resolve()),
        "processing_baseline": "05.00",
        "historical_processing_note": (
            "Reprocessed acquisition; not the paper's original L1C baseline."
        ),
    }
    (args.output / "scene-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
