#!/usr/bin/env python3
"""Build a campaign's echodata track PMTiles from its STAC Items.

Each georeferenced Item contributes one LineString feature (its geometry) with
``date``, ``time_start``, ``time_end``, ``point_count``, ``feature_type`` and
``campaign_id`` properties, in a single ``echodata`` layer — the schema web
viewers of oceanstream echodata tracks read. Requires tippecanoe >= 2.17,
which writes .pmtiles directly.

Usage:
    python scripts/hpc/build_campaign_tiles.py \\
        --collection s3://bucket/hpc/products/SD_X/collection.json \\
        --campaign-slug sd_x --out s3://bucket/tiles/sd_x_echodata.pmtiles
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("build_campaign_tiles")


def _so(url: str, endpoint: str) -> dict:
    if not url.startswith("s3://"):
        return {}
    ep = endpoint or os.environ.get("AWS_S3_ENDPOINT") or os.environ.get("S3_ENDPOINT_URL") or ""
    if ep and not ep.startswith(("http://", "https://")):
        ep = f"https://{ep}"
    return {"endpoint_url": ep} if ep else {}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--collection", required=True, help="URL of collection.json")
    p.add_argument("--campaign-slug", required=True, help="campaign_id property written on every feature")
    p.add_argument("--out", required=True, help="Output .pmtiles (local path or s3:// URL)")
    p.add_argument("--layer", default="echodata")
    p.add_argument("--minzoom", type=int, default=0)
    p.add_argument("--maxzoom", type=int, default=14)
    p.add_argument("--endpoint-url", default="")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    import fsspec

    tippecanoe = shutil.which("tippecanoe")
    if not tippecanoe:
        raise SystemExit("tippecanoe not found on PATH")

    fs, coll_path = fsspec.core.url_to_fs(args.collection, **_so(args.collection, args.endpoint_url))
    with fs.open(coll_path) as f:
        coll = json.load(f)
    base = coll_path.rsplit("/", 1)[0]

    features = []
    for link in coll.get("links", []):
        if link.get("rel") != "item":
            continue
        item_path = f"{base}/{link['href'].removeprefix('./')}"
        with fs.open(item_path) as f:
            item = json.load(f)
        geom = item.get("geometry")
        props = item["properties"]
        if not geom:
            logger.warning("%s: not georeferenced — skipped", props.get("oceanstream:day"))
            continue
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "date": props["oceanstream:day"],
                "time_start": props.get("start_datetime"),
                "time_end": props.get("end_datetime"),
                "point_count": len(geom.get("coordinates", [])),
                "feature_type": "echodata_track",
                "campaign_id": args.campaign_slug,
            },
        })
    if not features:
        logger.error("No georeferenced items in %s", args.collection)
        return 1
    logger.info("%d day track(s)", len(features))

    with tempfile.TemporaryDirectory() as tmp:
        gj = Path(tmp) / "tracks.geojson"
        gj.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        local_out = Path(tmp) / "out.pmtiles"
        # Same flags as the web app's own echodata tile builder.
        cmd = [tippecanoe, "-o", str(local_out), f"--layer={args.layer}",
               f"--minimum-zoom={args.minzoom}", f"--maximum-zoom={args.maxzoom}", "--force",
               "--no-feature-limit", "--no-tile-size-limit", "--no-line-simplification",
               "--no-tile-compression", "--quiet", str(gj)]
        subprocess.run(cmd, check=True)
        logger.info("tippecanoe wrote %.1f KB", local_out.stat().st_size / 1024)

        ofs, opath = fsspec.core.url_to_fs(args.out, **_so(args.out, args.endpoint_url))
        ofs.put(str(local_out), opath)
    logger.info("Uploaded %s", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
