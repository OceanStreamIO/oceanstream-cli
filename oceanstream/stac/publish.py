"""Publish STAC metadata for processed days in object storage.

Runs after a day's products have been pushed. For each day it

1. exports NASC to Hive-partitioned GeoParquet (``<day>/nasc_geoparquet/``) when a
   NASC store exists and no export does yet (the input for track tiles);
2. writes ``<day>/item.json`` last, so an Item's presence means the day is complete;

then rebuilds ``collection.json`` from every Item under the products root.
Re-running rewrites the Items and the Collection.

The Collection's descriptive fields (title, description, license, keywords,
providers, citation, platform and campaign blocks, extra links) come from the
campaign's metadata when it is given. When it is not, they are kept from the
Collection already published, so a run that only adds days never resets them.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from .echodata import (
    COLLECTION_FILENAME,
    ITEM_FILENAME,
    build_collection,
    build_day_item,
    write_json,
)

logger = logging.getLogger(__name__)

# Top-level Collection fields that come from the campaign, not from the Items.
GENERATED = {"type", "stac_version", "id", "extent", "summaries", "assets", "links"}
GENERATED_RELS = {"item", "self", "root"}


def storage_options(url: str, endpoint: str = "") -> dict[str, Any]:
    """fsspec options for *url*: the S3 endpoint for ``s3://`` URLs."""
    if not url.startswith("s3://"):
        return {}
    ep = (endpoint or os.environ.get("OCEANSTREAM_S3_ENDPOINT") or os.environ.get("AWS_S3_ENDPOINT")
          or os.environ.get("S3_ENDPOINT_URL") or "")
    if ep and not ep.startswith(("http://", "https://")):
        ep = f"https://{ep}"
    return {"endpoint_url": ep} if ep else {}


def export_nasc(fs: Any, root: str, day: str, cruise_id: str) -> bool:
    """Export every ``<day>--*--nasc.zarr`` to ``<day>/nasc_geoparquet``."""
    import xarray as xr  # noqa: PLC0415 - heavy, only for the export

    from oceanstream.echodata.compute import export_nasc_to_geoparquet  # noqa: PLC0415

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
                logger.warning("%s/%s: NASC has no positions, skipped", day, pulse)
                continue
            export_nasc_to_geoparquet(ds, output_dir=tmp, campaign_id=cruise_id,
                                      file_id=f"{day}_{pulse}")
            ds.close()
            wrote = True
        src = Path(tmp) / "nasc"
        if not wrote or not src.exists():
            return False
        fs.put(str(src), f"{day_dir}/nasc_geoparquet", recursive=True)
    logger.info("%s: exported NASC GeoParquet", day)
    return True


def kept_metadata(old: dict[str, Any]) -> dict[str, Any]:
    """The descriptive part of a published Collection, to carry into its rebuild."""
    meta = {k: v for k, v in old.items() if k not in GENERATED}
    links = [link for link in old.get("links", []) if link.get("rel") not in GENERATED_RELS]
    if links:
        meta["links"] = links
    return meta


def publish(  # noqa: PLR0913
    root_url: str,
    cruise_id: str,
    days: list[str] | None = None,
    *,
    run_id: str = "",
    metrics: dict[str, Any] | None = None,
    nasc_export: bool = True,
    assets: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
    title: str = "",
    public_base: str = "",
    endpoint: str = "",
    colormap: str = "ocean_r",
) -> dict[str, Any]:
    """Write Items for *days* (none: only rebuild the Collection); returns the Collection.

    *root_url* is the products root, e.g. ``s3://bucket/hpc/products/<cruise>``.
    *metrics* maps a day to the job's accounting, stored in its Item. *assets*
    adds Collection assets as ``key -> href``. *colormap* names echograms whose
    file names carry none.
    """
    import fsspec  # noqa: PLC0415 - optional dependency

    so = storage_options(root_url, endpoint)
    fs, root = fsspec.core.url_to_fs(root_url, **so)
    root = root.rstrip("/")
    scheme = root_url.split("://", 1)[0] + "://" if "://" in root_url else ""
    public = public_base.rstrip("/")
    metrics = metrics or {}

    for day in days or []:
        if not fs.exists(f"{root}/{day}"):
            raise FileNotFoundError(f"{day}: no products at {root}/{day}")
        if nasc_export:
            try:
                export_nasc(fs, root, day, cruise_id)
            except ImportError as e:
                # The export needs geopandas; the Items do not.
                logger.warning("%s: NASC GeoParquet export skipped (%s)", day, e)
                nasc_export = False
        props: dict[str, Any] = {}
        if run_id:
            props["oceanstream:run_id"] = run_id
        if day in metrics:
            props["oceanstream:metrics"] = metrics[day]
        item = build_day_item(
            f"{scheme}{root}/{day}", cruise_id, storage_options=so, properties=props,
            default_colormap=colormap,
            self_href=f"{public}/{day}/{ITEM_FILENAME}" if public else None,
        )
        write_json(fs, f"{root}/{day}/{ITEM_FILENAME}", item)
        logger.info("%s: %s written (%d assets, georeferenced=%s)", day, ITEM_FILENAME,
                    len(item["assets"]), item["properties"]["oceanstream:georeferenced"])

    items = []
    for path in sorted(fs.glob(f"{root}/*/{ITEM_FILENAME}")):
        with fs.open(path) as f:
            items.append(json.load(f))

    extra: dict[str, Any] = {}
    old_path = f"{root}/{COLLECTION_FILENAME}"
    if fs.exists(old_path):
        with fs.open(old_path) as f:
            old = json.load(f)
        extra = old.get("assets", {})  # keep assets registered earlier (e.g. tiles)
        if metadata is None:
            metadata = kept_metadata(old)
    for key, href in (assets or {}).items():
        extra[key] = {"href": href, "roles": ["data"],
                      "type": ("application/vnd.pmtiles" if href.endswith(".pmtiles")
                               else "application/octet-stream")}
    meta = dict(metadata or {})
    if title:
        meta["title"] = title
    coll = build_collection(
        items, cruise_id, extra_assets=extra or None,
        self_href=f"{public}/{COLLECTION_FILENAME}" if public else None, metadata=meta or None,
    )
    write_json(fs, old_path, coll)
    logger.info("%s written: %d item(s), extent %s", COLLECTION_FILENAME, len(items),
                coll["extent"])
    return coll
