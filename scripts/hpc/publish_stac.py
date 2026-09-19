#!/usr/bin/env python3
"""Publish STAC metadata for processed days in object storage.

Runs after products have been pushed. For each day it

  1. exports NASC to Hive-partitioned GeoParquet (``<day>/nasc_geoparquet/``)
     when a NASC store exists and no export does yet — the input for track
     tiles;
  2. writes ``<day>/item.json`` LAST, so an Item's presence means the day is
     complete;

then rebuilds ``collection.json`` from every Item under the products root.
Idempotent: re-running rewrites the Items and the Collection.

Usage:
    python scripts/hpc/publish_stac.py --root s3://bucket/hpc/products/SD_X \\
        --cruise-id SD_X --days 2023-10-10 [--run-id R] [--metrics-json m.json]
    python scripts/hpc/publish_stac.py --root s3://bucket/hpc/products/SD_X \\
        --cruise-id SD_X --collection-only [--asset tiles=https://...pmtiles]

S3 endpoint comes from --endpoint-url, AWS_S3_ENDPOINT or S3_ENDPOINT_URL.
``--public-base`` sets absolute self links (e.g. the bucket's HTTPS URL).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from oceanstream.stac.echodata import (  # noqa: E402
    COLLECTION_FILENAME,
    ITEM_FILENAME,
    build_collection,
    build_day_item,
    write_json,
)

logger = logging.getLogger("publish_stac")


def _storage_options(endpoint: str) -> dict:
    ep = endpoint or os.environ.get("AWS_S3_ENDPOINT") or os.environ.get("S3_ENDPOINT_URL") or ""
    if ep and not ep.startswith(("http://", "https://")):
        ep = f"https://{ep}"
    return {"endpoint_url": ep} if ep else {}


def export_nasc(fs, root: str, day: str, cruise_id: str, so: dict) -> bool:
    """Export every ``<day>--*--nasc.zarr`` to ``<day>/nasc_geoparquet``."""
    import xarray as xr
    from oceanstream.echodata.compute import export_nasc_to_geoparquet

    day_dir = f"{root}/{day}"
    stores = [p for p in fs.ls(day_dir, detail=False) if p.rstrip("/").endswith("--nasc.zarr")]
    if not stores:
        return False
    if fs.exists(f"{day_dir}/nasc_geoparquet"):
        logger.info("%s: nasc_geoparquet exists", day)
        return True

    with tempfile.TemporaryDirectory() as tmp:
        wrote = False
        for store in stores:
            pulse = store.rstrip("/").rsplit("/", 1)[-1].split("--")[1]
            ds = xr.open_zarr(fs.get_mapper(store), consolidated=None)
            if "latitude" not in ds.variables:
                logger.warning("%s/%s: NASC has no positions — skipped", day, pulse)
                continue
            export_nasc_to_geoparquet(ds, output_dir=tmp, campaign_id=cruise_id, file_id=f"{day}_{pulse}")
            ds.close()
            wrote = True
        src = Path(tmp) / "nasc"
        if not wrote or not src.exists():
            return False
        fs.put(str(src), f"{day_dir}/nasc_geoparquet", recursive=True)
    logger.info("%s: exported NASC GeoParquet", day)
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", required=True, help="Products root, e.g. s3://bucket/hpc/products/<cruise>")
    p.add_argument("--cruise-id", required=True)
    p.add_argument("--days", default="", help="Comma-separated days to (re)publish")
    p.add_argument("--run-id", default="")
    p.add_argument("--metrics-json", default="", help="JSON file: {day: {metric: value}} merged into Items")
    p.add_argument("--collection-only", action="store_true")
    p.add_argument("--skip-nasc-export", action="store_true")
    p.add_argument("--asset", action="append", default=[], help="Collection asset key=href (repeatable)")
    p.add_argument("--title", default="")
    p.add_argument("--public-base", default="", help="Public HTTPS URL of --root for absolute self links")
    p.add_argument("--endpoint-url", default="")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    import fsspec

    so = _storage_options(args.endpoint_url) if args.root.startswith("s3://") else {}
    fs, root = fsspec.core.url_to_fs(args.root, **so)
    root = root.rstrip("/")
    scheme = args.root.split("://", 1)[0] + "://" if "://" in args.root else ""
    public = args.public_base.rstrip("/")

    metrics = json.loads(Path(args.metrics_json).read_text()) if args.metrics_json else {}

    if not args.collection_only:
        days = [d for d in args.days.split(",") if d]
        if not days:
            p.error("--days is required unless --collection-only")
        for day in days:
            if not fs.exists(f"{root}/{day}"):
                logger.error("%s: no products at %s/%s", day, root, day)
                return 1
            if not args.skip_nasc_export:
                export_nasc(fs, root, day, args.cruise_id, so)
            props = {}
            if args.run_id:
                props["oceanstream:run_id"] = args.run_id
            if day in metrics:
                props["oceanstream:metrics"] = metrics[day]
            item = build_day_item(
                f"{scheme}{root}/{day}", args.cruise_id,
                storage_options=so, properties=props,
                self_href=f"{public}/{day}/{ITEM_FILENAME}" if public else None,
            )
            write_json(fs, f"{root}/{day}/{ITEM_FILENAME}", item)
            logger.info("%s: %s written (%d assets, georeferenced=%s)", day, ITEM_FILENAME,
                        len(item["assets"]), item["properties"]["oceanstream:georeferenced"])

    items = []
    for path in sorted(fs.glob(f"{root}/*/{ITEM_FILENAME}")):
        with fs.open(path) as f:
            items.append(json.load(f))

    extra = {}
    old_path = f"{root}/{COLLECTION_FILENAME}"
    if fs.exists(old_path):  # keep assets registered earlier (e.g. tiles)
        with fs.open(old_path) as f:
            extra = json.load(f).get("assets", {})
    for kv in args.asset:
        key, href = kv.split("=", 1)
        extra[key] = {"href": href, "roles": ["data"],
                      "type": "application/vnd.pmtiles" if href.endswith(".pmtiles") else "application/octet-stream"}

    coll = build_collection(
        items, args.cruise_id, title=args.title or None, extra_assets=extra or None,
        self_href=f"{public}/{COLLECTION_FILENAME}" if public else None,
    )
    write_json(fs, old_path, coll)
    logger.info("%s written: %d item(s), extent %s", COLLECTION_FILENAME, len(items), coll["extent"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
