"""Publish a campaign's products: STAC Items and Collection, then the track tiles.

The Collection takes the campaign's ``stac-collection.json`` (written by
``oceanstream campaign create --from-catalogue``) and the tiles take the slug from
its ``oceanstream-ingest.json``, so what the web app imports carries the
campaign's licence, providers, DOI and platform.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .console import log, ok, warn

if TYPE_CHECKING:
    from .cruise import RunConfig
    from .remote import Cluster

STAC_METADATA = "stac-collection.json"
INGEST = "oceanstream-ingest.json"


def products_root(cluster: Cluster, cfg: RunConfig) -> str:
    dest = cfg.dest or f"{cluster.site.s3_products_prefix}/{cfg.container}"
    return cluster.site.s3_uri(dest)


def campaign_metadata(campaign_dir: Path) -> dict[str, Any] | None:
    path = campaign_dir / STAC_METADATA
    return dict(json.loads(path.read_text())) if path.is_file() else None


def ingest_fields(campaign_dir: Path) -> dict[str, Any]:
    path = campaign_dir / INGEST
    return dict(json.loads(path.read_text())) if path.is_file() else {}


def publish_campaign(  # noqa: PLR0913
    cluster: Cluster, campaign_dir: Path, cfg: RunConfig, metrics: dict[str, Any] | None = None,
    *, days: list[str] | None = None, tiles: bool = True,
) -> dict[str, Any]:
    """Items for the pushed *days* (default: all of the campaign's), Collection, tiles."""
    from oceanstream.stac.publish import publish  # noqa: PLC0415 - needs fsspec
    from oceanstream.stac.tracks import build_track_tiles  # noqa: PLC0415

    from .cruise import State  # noqa: PLC0415 - cruise imports this module

    root = products_root(cluster, cfg)
    if days is None:
        days = sorted(State(campaign_dir / "state.tsv").pushed())
    log(f"Publishing STAC for {len(days)} day(s) at {root}")
    coll = publish(root, cfg.container, days, metrics=metrics,
                   metadata=campaign_metadata(campaign_dir), endpoint=cluster.site.s3_endpoint)
    out: dict[str, Any] = {"collection": f"{root}/collection.json",
                           "items": len(coll["summaries"]["oceanstream:days"])}
    slug = ingest_fields(campaign_dir).get("campaignSlug", "")
    if tiles and slug:
        tiles_url = cluster.site.s3_uri(f"tiles/{slug}_echodata.pmtiles")
        try:
            build_track_tiles(f"{root}/collection.json", slug, tiles_url,
                              endpoint=cluster.site.s3_endpoint)
            out["tiles"] = tiles_url
            ok(f"Track tiles at {tiles_url}")
        except RuntimeError as e:
            warn(f"No track tiles: {e}")
    return out
