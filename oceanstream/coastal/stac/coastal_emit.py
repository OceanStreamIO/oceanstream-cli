"""STAC 1.0 emission for coastal products.

A STAC item is where a run stops being a directory of files and starts being
something another system will index, search and act on. So the thing that
matters most here is not the geometry or the asset list — it is that the
**quality verdict travels in the item itself**, in
``properties["oceanstream:qc"]``, next to the assets it applies to.

The alternative, which is what most pipelines do, is to write the verdict into
a log or a sidecar report. That works exactly until someone queries the
catalogue, gets a footprint back, and reads the depth map without ever seeing
the report. A scene whose empirical ``k`` sits an order of magnitude below the
pure-water floor produces a perfectly well-formed COG; nothing about the file
says the retrieval was not physical. Putting the verdict in the item is what
makes the failure visible to a consumer who never reads the documentation.

Two smaller decisions follow from the same reasoning:

* ``properties["oceanstream:status"]`` is ``diagnostic_only`` for a failed
  scene, and the item description says so in its first sentence — before the
  sensor, before the date, before anything a reader would skim past.
* Every asset carries its product's caveat in ``description``. The caveat is
  already in the GeoTIFF tags; repeating it here means a client that reads only
  the catalogue still gets it.

Mirrors :mod:`oceanstream.echodata.stac.echodata_emit` in layout —
``<output>/stac/collection.json`` plus ``<output>/stac/items/<id>.json``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oceanstream.coastal.products import (
    STATUS_DIAGNOSTIC,
    STATUS_PUBLISHABLE,
    WrittenProduct,
    jsonable,
)

if TYPE_CHECKING:
    from oceanstream.coastal.aoi import AOI
    from oceanstream.coastal.io.rasters import RasterGrid
    from oceanstream.coastal.scene import Scene

logger = logging.getLogger(__name__)

STAC_VERSION = "1.0.0"

#: Declared so a consumer can tell a coastal item apart from any other
#: oceanstream item without parsing asset keys.
PROCESSING_EXTENSION = "https://stac-extensions.github.io/processing/v1.1.0/schema.json"
EO_EXTENSION = "https://stac-extensions.github.io/eo/v1.1.0/schema.json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase, hyphen-separated, safe as a filename and a STAC id."""
    return _SLUG_RE.sub("-", str(value).strip().lower()).strip("-") or "unnamed"


def item_id(aoi_name: str, sensor: str, acquisition_date: str) -> str:
    """Deterministic item id, so re-running a scene replaces it rather than duplicating it."""
    return f"{slugify(aoi_name)}-{slugify(sensor)}-{slugify(acquisition_date)}"


def collection_id_for(aoi_name: str) -> str:
    return f"coastal-{slugify(aoi_name)}"


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def geographic_bounds(
    grid: RasterGrid, fallback: Sequence[float] | None = None
) -> tuple[float, float, float, float]:
    """The grid's footprint as (west, south, east, north) in EPSG:4326.

    A projected grid must be reprojected, not passed through. UTM eastings and
    northings are numerically valid longitudes and latitudes only in the sense
    that a parser will accept them — a Sesimbra scene in EPSG:32629 would be
    catalogued somewhere south of Ghana. When rasterio is unavailable and no
    fallback bbox is supplied this raises rather than guessing, because a wrong
    footprint in a catalogue is worse than a missing item.
    """
    west, south, east, north = grid.bounds
    if grid.is_geographic:
        return (float(west), float(south), float(east), float(north))
    try:
        from rasterio.warp import transform_bounds
    except ImportError as exc:  # pragma: no cover - depends on optional install
        if fallback is not None:
            logger.warning(
                "rasterio unavailable; using the supplied bbox for the STAC "
                "footprint instead of reprojecting the %s grid.",
                grid.crs,
            )
            w, s, e, n = (float(v) for v in fallback)
            return (w, s, e, n)
        raise RuntimeError(
            f"Grid CRS is {grid.crs}, which must be reprojected to EPSG:4326 for "
            "STAC, but rasterio is not installed and no fallback bbox was given. "
            "Install the 'coastal' extra or pass the AOI so its processing_bbox "
            "can be used."
        ) from exc
    w, s, e, n = transform_bounds(grid.crs, "EPSG:4326", west, south, east, north)
    return (float(w), float(s), float(e), float(n))


def bbox_polygon(bbox: Sequence[float]) -> dict[str, Any]:
    """A closed rectangular ring for a (west, south, east, north) bbox."""
    west, south, east, north = (float(v) for v in bbox)
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


# ---------------------------------------------------------------------------
# Item
# ---------------------------------------------------------------------------


def _asset_from_product(product: WrittenProduct, stac_dir: Path) -> dict[str, Any]:
    """One STAC asset, with the product's caveat carried into its description."""
    description = product.spec.description
    if product.spec.caveat:
        description = f"{description} {product.spec.caveat}"

    asset: dict[str, Any] = {
        "href": _relative_href(product.href, stac_dir),
        "type": product.spec.media_type,
        "title": product.spec.title,
        "description": description,
        "roles": list(product.spec.roles),
    }
    if product.spec.unit:
        asset["oceanstream:unit"] = product.spec.unit
    if product.stats:
        asset["oceanstream:stats"] = jsonable(product.stats)
    if product.properties:
        asset["oceanstream:properties"] = jsonable(product.properties)
    asset["oceanstream:status"] = product.status
    return asset


def _relative_href(href: str, stac_dir: Path) -> str:
    """Prefer a path relative to the STAC directory; keep cloud URIs absolute.

    Uses :func:`os.path.relpath` rather than :meth:`Path.relative_to` because
    the catalogue sits *below* the products it describes — items land in
    ``<root>/stac/items/`` while the rasters land in ``<root>/`` — so every
    href needs to walk back up. ``relative_to`` only descends, and falling back
    to an absolute path would produce a catalogue that stops resolving the
    moment anyone copies or serves it from a different root.
    """
    if "://" in href:
        return href
    try:
        return os.path.relpath(Path(href).resolve(), stac_dir.resolve())
    except ValueError:
        # Different drives on Windows — no relative path exists.
        return href


def build_item(
    *,
    products: Mapping[str, WrittenProduct],
    grid: RasterGrid,
    qc_verdict: Mapping[str, Any],
    aoi_name: str,
    sensor: str,
    acquisition_date: str,
    stac_dir: Path,
    collection: str | None = None,
    bbox: Sequence[float] | None = None,
    detectability: Mapping[str, Any] | None = None,
    attenuation: Mapping[str, Any] | None = None,
    scene_provenance: Mapping[str, Any] | None = None,
    extra_properties: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a STAC item for one processed scene.

    Pure: builds and returns the dictionary without touching the filesystem, so
    the item's contents can be asserted on directly.
    """
    if "passed" not in qc_verdict:
        raise ValueError(
            "qc_verdict must carry a 'passed' key — pass the output of "
            "oceanstream.coastal.qc.floors.scene_floor_verdict(). An item "
            "published without a verdict is indistinguishable from one that "
            "passed."
        )
    passed = bool(qc_verdict["passed"])
    status = STATUS_PUBLISHABLE if passed else STATUS_DIAGNOSTIC

    footprint = tuple(float(v) for v in bbox) if bbox else geographic_bounds(grid)
    coll = collection or collection_id_for(aoi_name)
    identifier = item_id(aoi_name, sensor, acquisition_date)

    summary = str(qc_verdict.get("summary", "")).strip()
    if passed:
        description = (
            f"Coastal benthic retrieval for {aoi_name} from {sensor} on {acquisition_date}."
        )
    else:
        description = (
            f"DIAGNOSTIC ONLY — this scene failed quality control ({summary}). "
            f"Coastal benthic retrieval for {aoi_name} from {sensor} on "
            f"{acquisition_date}. The products are readable and internally "
            "consistent but the retrieval is not physically supported; do not "
            "use them as measurements."
        )

    properties: dict[str, Any] = {
        "datetime": _as_datetime(acquisition_date),
        "description": description,
        "platform": sensor,
        "processing:level": "L2",
        "processing:software": {"oceanstream": _library_version()},
        "created": datetime.now(UTC).isoformat(),
        "oceanstream:status": status,
        "oceanstream:aoi": aoi_name,
        "oceanstream:qc": jsonable(qc_verdict),
        "oceanstream:grid": jsonable(grid.to_dict()),
    }
    if detectability:
        properties["oceanstream:detectability"] = jsonable(detectability)
    if attenuation:
        properties["oceanstream:attenuation"] = jsonable(attenuation)
    if scene_provenance:
        properties["oceanstream:scene"] = jsonable(scene_provenance)
    if extra_properties:
        properties.update(jsonable(dict(extra_properties)))

    return {
        "type": "Feature",
        "stac_version": STAC_VERSION,
        "stac_extensions": [PROCESSING_EXTENSION, EO_EXTENSION],
        "id": identifier,
        "collection": coll,
        "bbox": list(footprint),
        "geometry": bbox_polygon(footprint),
        "properties": properties,
        "assets": {
            key: _asset_from_product(product, stac_dir) for key, product in sorted(products.items())
        },
        "links": [
            {"rel": "self", "href": f"{identifier}.json"},
            {"rel": "collection", "href": "../collection.json"},
            {"rel": "parent", "href": "../collection.json"},
        ],
    }


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def build_collection(
    *,
    collection: str,
    aoi_name: str,
    bbox: Sequence[float],
    interval: Sequence[str | None],
    description: str | None = None,
    keywords: Sequence[str] | None = None,
    license_id: str = "CC-BY-4.0",
) -> dict[str, Any]:
    """Build an empty coastal collection covering one AOI."""
    return {
        "type": "Collection",
        "stac_version": STAC_VERSION,
        "id": collection,
        "title": f"Coastal benthic products — {aoi_name}",
        "description": description
        or (
            f"Benthic reflectance, satellite-derived bathymetry and detection "
            f"limits for {aoi_name}, retrieved by closed-form Lee inversion over "
            "atmospherically corrected multispectral imagery. Items carry their "
            "own quality verdict in properties['oceanstream:qc']; an item with "
            f"oceanstream:status = '{STATUS_DIAGNOSTIC}' failed a physical gate "
            "and must not be read as a measurement."
        ),
        "license": license_id,
        "keywords": list(keywords or ["benthic", "bathymetry", "optics", "coastal", aoi_name]),
        "extent": {
            "spatial": {"bbox": [[float(v) for v in bbox]]},
            "temporal": {"interval": [list(interval)]},
        },
        "summaries": {
            "processing": {
                "software": "oceanstream",
                "version": _library_version(),
                "processing_level": "L2",
            },
        },
        "links": [
            {"rel": "self", "href": "collection.json"},
            {"rel": "items", "href": "items/"},
        ],
        "assets": {},
    }


def merge_item_into_collection(
    collection: dict[str, Any], item: Mapping[str, Any]
) -> dict[str, Any]:
    """Widen the collection's extent and register the item, idempotently.

    Re-running a scene rewrites its item rather than appending a second link,
    which is why item ids are deterministic. The extent is widened rather than
    replaced so a partially reprocessed collection never reports a footprint
    smaller than the items it actually holds.
    """
    item_bbox = [float(v) for v in item["bbox"]]
    existing = collection.get("extent", {}).get("spatial", {}).get("bbox") or [item_bbox]
    current = [float(v) for v in existing[0]]
    collection.setdefault("extent", {}).setdefault("spatial", {})["bbox"] = [
        [
            min(current[0], item_bbox[0]),
            min(current[1], item_bbox[1]),
            max(current[2], item_bbox[2]),
            max(current[3], item_bbox[3]),
        ]
    ]

    when = item.get("properties", {}).get("datetime")
    interval = collection["extent"].setdefault("temporal", {}).get("interval") or [[when, when]]
    start, end = interval[0]
    collection["extent"]["temporal"]["interval"] = [
        [
            min(v for v in (start, when) if v) if (start or when) else None,
            max(v for v in (end, when) if v) if (end or when) else None,
        ]
    ]

    href = f"items/{item['id']}.json"
    links = [ln for ln in collection.get("links", []) if ln.get("href") != href]
    links.append({"rel": "item", "href": href, "type": "application/json"})
    collection["links"] = links

    statuses = collection.setdefault("summaries", {}).setdefault("oceanstream:status", [])
    status = item["properties"].get("oceanstream:status")
    if status and status not in statuses:
        statuses.append(status)
    return collection


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def emit_stac(
    output_dir: str | Path,
    *,
    products: Mapping[str, WrittenProduct],
    grid: RasterGrid,
    qc_verdict: Mapping[str, Any],
    aoi: AOI | None = None,
    aoi_name: str | None = None,
    scene: Scene | None = None,
    sensor: str | None = None,
    acquisition_date: str | None = None,
    detectability: Mapping[str, Any] | None = None,
    attenuation: Mapping[str, Any] | None = None,
    collection: str | None = None,
    extra_properties: Mapping[str, Any] | None = None,
) -> tuple[Path, Path]:
    """Write ``stac/collection.json`` and ``stac/items/<id>.json``.

    Accepts either the rich objects (``aoi``, ``scene``) or the three strings
    they would supply. The objects win when both are given; the strings exist so
    the emitter can be tested and reused without constructing a full
    :class:`~oceanstream.coastal.scene.Scene`.

    Cloud output directories are not supported here: STAC emission reads back an
    existing collection to merge into, and a read-modify-write against object
    storage needs a locking story this library does not have. Write locally,
    then sync.

    Returns
    -------
    (collection_path, item_path)
    """
    name = aoi_name or (aoi.name if aoi else None)
    if not name:
        raise ValueError("emit_stac needs an AOI or an aoi_name to identify the item.")
    # Scene.sensor is a resolved SensorProfile, not a string.
    sensor_name = sensor or (scene.sensor.name if scene else None)
    if not sensor_name:
        raise ValueError("emit_stac needs a Scene or a sensor name.")
    raw_date = acquisition_date or (scene.acquisition_date if scene else None)
    if not raw_date:
        raise ValueError("emit_stac needs a Scene or an acquisition_date.")
    date = raw_date if isinstance(raw_date, str) else raw_date.isoformat()

    out = Path(str(output_dir))
    if "://" in str(output_dir):
        raise ValueError(
            f"emit_stac writes to a local directory, got {output_dir!r}. The "
            "collection has to be read back to merge a new item into it, which "
            "is a read-modify-write this library will not attempt against "
            "object storage. Emit locally, then upload the stac/ tree."
        )

    stac_dir = out / "stac"
    items_dir = stac_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)

    fallback_bbox = aoi.processing_bbox if aoi else None
    footprint = geographic_bounds(grid, fallback=fallback_bbox)
    coll_id = collection or collection_id_for(name)

    item = build_item(
        products=products,
        grid=grid,
        qc_verdict=qc_verdict,
        aoi_name=name,
        sensor=sensor_name,
        acquisition_date=date,
        stac_dir=items_dir,
        collection=coll_id,
        bbox=footprint,
        detectability=detectability,
        attenuation=attenuation,
        scene_provenance=scene.describe() if scene else None,
        extra_properties=extra_properties,
    )

    if scene is not None and scene.acquisition_datetime is not None:
        item["properties"]["datetime"] = _as_datetime(scene.acquisition_datetime.isoformat())

    collection_path = stac_dir / "collection.json"
    if collection_path.exists():
        doc = json.loads(collection_path.read_text())
    else:
        doc = build_collection(
            collection=coll_id,
            aoi_name=aoi.display_label if aoi else name,
            bbox=footprint,
            interval=[item["properties"]["datetime"], item["properties"]["datetime"]],
        )
    doc = merge_item_into_collection(doc, item)

    item_path = items_dir / f"{item['id']}.json"
    item_path.write_text(json.dumps(item, indent=2))
    collection_path.write_text(json.dumps(doc, indent=2))

    logger.info(
        "Wrote STAC item %s (%s) with %d assets to %s",
        item["id"],
        item["properties"]["oceanstream:status"],
        len(item["assets"]),
        item_path,
    )
    return collection_path, item_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_datetime(value: str) -> str:
    """Normalise a date or datetime string to RFC 3339, which STAC requires."""
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y%m%d", "%Y-%m-%d %H:%M:%S", "%Y%m%dT%H%M%S"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            raise ValueError(
                f"Could not read {value!r} as a date. STAC requires an RFC 3339 "
                "datetime; pass an ISO date such as '2026-06-27'."
            ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat().replace("+00:00", "Z")


def _library_version() -> str:
    try:
        from importlib.metadata import version

        return version("oceanstream")
    except Exception:  # pragma: no cover - version metadata is environment-specific
        return "unknown"
