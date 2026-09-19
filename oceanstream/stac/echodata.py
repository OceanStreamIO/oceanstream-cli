"""STAC Items and Collections for processed echosounder days.

One Item describes one processed day: every Zarr store, NetCDF export,
echogram and GeoParquet export in the day's directory becomes an asset with a
relative href. The Item is the record consumers (e.g. a web app importer) use
to discover products, so filenames never have to be parsed downstream.

Works on any fsspec path — a local directory or ``s3://bucket/prefix`` with
``storage_options`` — and reads only coordinates and consolidated metadata,
never the bulk arrays.

Day directory layout (as written by ``process_from_raw.py``)::

    <day>/<day>--<pulse>.zarr                  Sv
    <day>/<day>--<pulse>--<product>.zarr       denoised | pruned | mvbs | nasc | ...
    <day>/<day>--<pulse>--<product>.nc         NetCDF exports
    <day>/<variant>/<day>--<pulse>[--<v>]--<cmap>_<channel>[--thumb].png
    <day>/nasc_geoparquet/                     Hive-partitioned GeoParquet (optional)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

STAC_VERSION = "1.0.0"
PROCESSING_EXT = "https://stac-extensions.github.io/processing/v1.2.0/schema.json"
ITEM_FILENAME = "item.json"
COLLECTION_FILENAME = "collection.json"

_ZARR_RE = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})--(?P<pulse>[a-z_]+?)(?:--(?P<product>[a-z0-9_-]+))?\.zarr$")
_NC_RE = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})--(?P<pulse>[a-z_]+?)(?:--(?P<product>[a-z0-9_-]+))?\.nc$")
_PNG_RE = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2})--(?P<pulse>[a-z_]+?)(?:--(?P<variant>[a-z-]+))?"
    r"--(?P<cmap>[A-Za-z0-9]+(?:_r)?)_(?P<channel>.+?)(?P<thumb>--thumb)?\.png$"
)
_POS_VARS = (("latitude", "longitude"), ("lat", "lon"))


def _iso(ts: Any) -> Optional[str]:
    import pandas as pd

    if ts is None:
        return None
    t = pd.Timestamp(ts)
    if pd.isna(t):
        return None
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.isoformat().replace("+00:00", "Z")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _product_key(pulse: str, product: Optional[str]) -> str:
    return f"{pulse}_{(product or 'sv').replace('-', '_')}"


def _open_zarr(fs, path: str):
    import xarray as xr

    return xr.open_zarr(fs.get_mapper(path), consolidated=None, chunks=None, decode_timedelta=False)


def _track(ds, max_points: int) -> Optional[list[list[float]]]:
    """Downsampled [lon, lat] track from a dataset's position variables."""
    import numpy as np

    for lat_name, lon_name in _POS_VARS:
        if lat_name in ds.variables and lon_name in ds.variables:
            lat = np.asarray(ds[lat_name].values, dtype=float).ravel()
            lon = np.asarray(ds[lon_name].values, dtype=float).ravel()
            ok = np.isfinite(lat) & np.isfinite(lon)
            lat, lon = lat[ok], lon[ok]
            if lat.size < 2:
                return None
            step = max(1, int(np.ceil(lat.size / max_points)))
            coords = [[round(float(x), 6), round(float(y), 6)] for x, y in zip(lon[::step], lat[::step])]
            if coords[-1] != [round(float(lon[-1]), 6), round(float(lat[-1]), 6)]:
                coords.append([round(float(lon[-1]), 6), round(float(lat[-1]), 6)])
            return coords
    return None


def build_day_item(
    day_path: str,
    cruise_id: str,
    *,
    storage_options: Optional[dict] = None,
    collection_id: Optional[str] = None,
    self_href: Optional[str] = None,
    properties: Optional[dict] = None,
    max_track_points: int = 500,
) -> dict:
    """Build a STAC Item (as a dict) describing one processed day.

    Args:
        day_path: Directory of the day, e.g. ``s3://bucket/hpc/products/C/2023-10-10``.
        cruise_id: Cruise / campaign identifier.
        storage_options: fsspec options (e.g. ``{"endpoint_url": ...}`` for S3).
        collection_id: Collection this Item belongs to (default: cruise_id).
        self_href: Absolute URL the Item will be published at.
        properties: Extra properties merged in last (e.g. run id, job metrics).
        max_track_points: Upper bound on LineString vertices.
    """
    import fsspec
    import numpy as np

    fs, root = fsspec.core.url_to_fs(day_path, **(storage_options or {}))
    root = root.rstrip("/")
    day = root.rsplit("/", 1)[-1]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError(f"Day directory must end in YYYY-MM-DD: {day_path}")

    entries = fs.ls(root, detail=True)
    assets: dict[str, dict] = {}
    t_min = t_max = None
    frequencies: set[float] = set()
    track: Optional[list[list[float]]] = None
    track_rank = -1  # prefer positions from nasc > pruned > denoised > sv
    rank = {"nasc": 4, "pruned": 3, "denoised": 2, None: 1}

    for e in sorted(entries, key=lambda d: d["name"]):
        name = e["name"].rstrip("/").rsplit("/", 1)[-1]
        full = f"{root}/{name}"

        m = _ZARR_RE.match(name)
        if m and m["day"] == day:
            pulse, product = m["pulse"], m["product"]
            asset: dict[str, Any] = {
                "href": f"./{name}",
                "type": "application/vnd+zarr",
                "roles": ["data"],
                "title": f"{pulse} {product or 'Sv'}",
                "oceanstream:pulse": pulse,
                "oceanstream:product": product or "sv",
            }
            try:
                ds = _open_zarr(fs, full)
                asset["oceanstream:dims"] = {k: int(v) for k, v in ds.sizes.items()}
                asset["oceanstream:variables"] = sorted(ds.data_vars)
                if "ping_time" in ds.coords and ds.sizes.get("ping_time", 0):
                    pt = ds["ping_time"].values
                    lo, hi = np.nanmin(pt), np.nanmax(pt)
                    t_min = lo if t_min is None or lo < t_min else t_min
                    t_max = hi if t_max is None or hi > t_max else t_max
                if "frequency_nominal" in ds.variables:
                    frequencies.update(float(f) for f in np.ravel(ds["frequency_nominal"].values) if np.isfinite(f))
                r = rank.get(product, 0)
                if r > track_rank:
                    t = _track(ds, max_track_points)
                    if t:
                        track, track_rank = t, r
                ds.close()
            except Exception as exc:  # keep the asset, note the problem
                logger.warning("Could not read %s: %s", full, exc)
                asset["oceanstream:read_error"] = str(exc)[:200]
            assets[_product_key(pulse, product)] = asset
            continue

        m = _NC_RE.match(name)
        if m and m["day"] == day:
            assets[_product_key(m["pulse"], m["product"]) + "_netcdf"] = {
                "href": f"./{name}",
                "type": "application/x-netcdf",
                "roles": ["data"],
                "file:size": int(e.get("size") or 0),
                "oceanstream:pulse": m["pulse"],
                "oceanstream:product": m["product"] or "sv",
            }
            continue

        if name == "nasc_geoparquet" and e["type"] == "directory":
            assets["nasc_geoparquet"] = {
                "href": "./nasc_geoparquet/",
                "type": "application/vnd.apache.parquet",
                "roles": ["data"],
                "title": "NASC cells (Hive-partitioned GeoParquet)",
            }
            continue

        if e["type"] == "directory":  # echogram folders
            for png in sorted(fs.find(full)):
                pname = png.rsplit("/", 1)[-1]
                pm = _PNG_RE.match(pname)
                if not pm or pm["day"] != day:
                    continue
                variant = pm["variant"] or name  # folder name when unnamed (raw)
                thumb = bool(pm["thumb"])
                key = "_".join(
                    ["echogram", pm["pulse"], _slug(variant), _slug(pm["cmap"]), _slug(pm["channel"])]
                    + (["thumb"] if thumb else [])
                )
                assets[key] = {
                    "href": f"./{name}/{pname}",
                    "type": "image/png",
                    "roles": ["thumbnail"] if thumb else ["visual"],
                    "oceanstream:pulse": pm["pulse"],
                    "oceanstream:variant": variant,
                    "oceanstream:colormap": pm["cmap"],
                    "oceanstream:channel": pm["channel"],
                }

    if track:
        lons = [c[0] for c in track]
        lats = [c[1] for c in track]
        bbox = [min(lons), min(lats), max(lons), max(lats)]
        geometry: Optional[dict] = {"type": "LineString", "coordinates": track}
    else:
        bbox, geometry = None, None

    props: dict[str, Any] = {
        "datetime": None,
        "start_datetime": _iso(t_min) or f"{day}T00:00:00Z",
        "end_datetime": _iso(t_max) or f"{day}T23:59:59Z",
        "oceanstream:cruise_id": cruise_id,
        "oceanstream:day": day,
        "oceanstream:frequencies": sorted(frequencies),
        "oceanstream:georeferenced": geometry is not None,
        "created": _iso(datetime.now(timezone.utc)),
    }
    props.update(properties or {})

    item: dict[str, Any] = {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "stac_extensions": [PROCESSING_EXT],
        "id": f"{cruise_id}_{day}",
        "collection": collection_id or cruise_id,
        "geometry": geometry,
        "properties": props,
        "assets": assets,
        "links": [
            {"rel": "collection", "href": f"../{COLLECTION_FILENAME}", "type": "application/json"},
            {"rel": "parent", "href": f"../{COLLECTION_FILENAME}", "type": "application/json"},
            {"rel": "root", "href": f"../{COLLECTION_FILENAME}", "type": "application/json"},
        ],
    }
    if bbox:
        item["bbox"] = bbox
    if self_href:
        item["links"].append({"rel": "self", "href": self_href, "type": "application/geo+json"})
    return item


def build_collection(
    items: list[dict],
    collection_id: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    extra_assets: Optional[dict] = None,
    self_href: Optional[str] = None,
) -> dict:
    """Build a Collection from Items; extents are recomputed from the Items."""
    bboxes = [i["bbox"] for i in items if i.get("bbox")]
    starts = sorted(i["properties"]["start_datetime"] for i in items if i["properties"].get("start_datetime"))
    ends = sorted(i["properties"]["end_datetime"] for i in items if i["properties"].get("end_datetime"))
    spatial = (
        [[min(b[0] for b in bboxes), min(b[1] for b in bboxes), max(b[2] for b in bboxes), max(b[3] for b in bboxes)]]
        if bboxes else [[-180.0, -90.0, 180.0, 90.0]]
    )
    days = sorted(i["properties"]["oceanstream:day"] for i in items)
    coll: dict[str, Any] = {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": collection_id,
        "title": title or collection_id,
        "description": description or f"Processed echosounder products for {collection_id}.",
        "license": "proprietary",
        "extent": {
            "spatial": {"bbox": spatial},
            "temporal": {"interval": [[starts[0] if starts else None, ends[-1] if ends else None]]},
        },
        "summaries": {
            "oceanstream:frequencies": sorted({f for i in items for f in i["properties"].get("oceanstream:frequencies", [])}),
            "oceanstream:days": days,
        },
        "links": [
            {"rel": "item", "href": f"./{i['properties']['oceanstream:day']}/{ITEM_FILENAME}", "type": "application/geo+json"}
            for i in sorted(items, key=lambda i: i["properties"]["oceanstream:day"])
        ],
    }
    if extra_assets:
        coll["assets"] = extra_assets
    if self_href:
        coll["links"].append({"rel": "self", "href": self_href, "type": "application/json"})
        coll["links"].append({"rel": "root", "href": self_href, "type": "application/json"})
    return coll


def write_json(fs, path: str, doc: dict) -> None:
    with fs.open(path, "w") as f:
        f.write(json.dumps(doc, indent=2, default=str))
