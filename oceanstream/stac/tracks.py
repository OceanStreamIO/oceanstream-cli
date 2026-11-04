"""A campaign's echodata track PMTiles, from its STAC Items.

Each georeferenced Item gives one feature (its geometry) with ``date``,
``time_start``, ``time_end``, ``point_count``, ``feature_type`` and ``campaign_id``
properties, in one ``echodata`` layer: the schema the web app's echodata track
layer reads (Martin source ``<slug>_echodata``). Needs tippecanoe >= 2.17.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .publish import storage_options

logger = logging.getLogger(__name__)


def track_features(collection: dict[str, Any], read_item: Any, campaign_slug: str
                   ) -> list[dict[str, Any]]:
    """One feature per georeferenced Item; *read_item* maps an item href to its JSON."""
    features = []
    for link in collection.get("links", []):
        if link.get("rel") != "item":
            continue
        item = read_item(link["href"].removeprefix("./"))
        geom = item.get("geometry")
        props = item["properties"]
        if not geom:
            logger.warning("%s: not georeferenced, skipped", props.get("oceanstream:day"))
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
                "campaign_id": campaign_slug,
            },
        })
    return features


def build_track_tiles(collection_url: str, campaign_slug: str, out_url: str, *,  # noqa: PLR0913
                      layer: str = "echodata", minzoom: int = 0, maxzoom: int = 14,
                      endpoint: str = "") -> int:
    """Write the track tiles of *collection_url* to *out_url*; returns the feature count."""
    import fsspec  # noqa: PLC0415 - optional dependency

    tippecanoe = shutil.which("tippecanoe")
    if not tippecanoe:
        raise RuntimeError("tippecanoe not found on PATH (needs >= 2.17).")
    fs, coll_path = fsspec.core.url_to_fs(collection_url,
                                          **storage_options(collection_url, endpoint))
    with fs.open(coll_path) as f:
        coll = json.load(f)
    base = coll_path.rsplit("/", 1)[0]

    def read_item(rel: str) -> dict[str, Any]:
        with fs.open(f"{base}/{rel}") as f:
            return dict(json.load(f))

    features = track_features(coll, read_item, campaign_slug)
    if not features:
        raise RuntimeError(f"No georeferenced items in {collection_url}")
    logger.info("%d day track(s)", len(features))
    with tempfile.TemporaryDirectory() as tmp:
        gj = Path(tmp) / "tracks.geojson"
        gj.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
        local_out = Path(tmp) / "out.pmtiles"
        # Same flags as the web app's own echodata tile builder.
        subprocess.run([tippecanoe, "-o", str(local_out), f"--layer={layer}",
                        f"--minimum-zoom={minzoom}", f"--maximum-zoom={maxzoom}", "--force",
                        "--no-feature-limit", "--no-tile-size-limit", "--no-line-simplification",
                        "--no-tile-compression", "--quiet", str(gj)], check=True)
        logger.info("tippecanoe wrote %.1f KB", local_out.stat().st_size / 1024)
        ofs, opath = fsspec.core.url_to_fs(out_url, **storage_options(out_url, endpoint))
        ofs.put(str(local_out), opath)
    logger.info("Uploaded %s", out_url)
    return len(features)
