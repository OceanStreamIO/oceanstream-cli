"""Scene screening via Planetary Computer — which bundles are worth downloading.

An L1C bundle is ~800 MB and an ACOLITE run is tens of minutes, so the
expensive question is which dates to bother with. Sentinel-2's tile-level
``eo:cloud_cover`` cannot answer it: the metric describes 110 x 110 km, a
coastal AOI occupies a fraction of a percent of that, and the two are only
loosely related. The prototype found scenes with 45% tile cloud that were
perfectly clear over the dive area, and scenes at 15% that were hazy over it.

So this module reads the imagery instead. It pulls red and NIR from the
already-atmospherically-corrected L2A product inside the AOI window, masks to
water using NIR, and reports the median over-water red reflectance. L2A is used
as a **pre-filter only** — the retrieval itself runs on L1C through ACOLITE,
because Sen2Cor is not built for water and its over-water output is not
suitable for an optical inversion.

The thresholds are site-dependent, and that matters
---------------------------------------------------
``CLEAR_MAX`` / ``HAZY_MIN`` (0.03 / 0.06) come from the prototype and were
tuned on Sesimbra: clear, near-zero-CDOM Iberian water. Over-water red rises
for at least three unrelated reasons — atmospheric haze, suspended sediment,
and a bright shallow seabed — and this measurement cannot separate them.
Applied unchanged to a turbid Irish or peat-stained Scottish AOI it will
reject scenes whose red is high because *the water is genuinely different*,
which is precisely the optical diversity a multi-AOI validation exists to
sample. Hence :func:`screen_dates` always returns the measured number
alongside the verdict, and the thresholds are arguments.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

#: Prototype thresholds on median over-water red reflectance. Tuned on clear
#: Iberian water — see the module docstring before reusing them elsewhere.
CLEAR_MAX = 0.03
HAZY_MIN = 0.06

#: Below this many water pixels the median is not a measurement. The AOI window
#: is small by design, so this is a low bar.
MIN_WATER_PIXELS = 200

#: Sentinel-2 processing baseline 4.0 (Jan 2022) introduced a -1000 radiometric
#: offset. Ignoring it shifts every reflectance by 0.1 — which would push every
#: post-2022 scene straight past HAZY_MIN and reject the entire archive.
BASELINE_WITH_OFFSET = 4.0
BOA_ADD_OFFSET = -1000.0
QUANTIFICATION_VALUE = 10000.0

#: Sentinel-2 reflectance bands use DN 0 as the nodata sentinel.
#:
#: It has to be masked *before* the baseline-4.0 offset, or the sentinel stops
#: looking like a gap and starts looking like a measurement: it lands on
#: exactly ``BOA_ADD_OFFSET / QUANTIFICATION_VALUE`` = -0.1, which sits below
#: :data:`WATER_NIR_MAX`. An all-nodata window then reads as wall-to-wall water
#: with a median red of -0.1 — the darkest in the search, and so the
#: best-ranked scene rather than the discarded one.
#:
#: The coverage check cannot catch this. Sentinel-2 tiles carry wide nodata
#: margins along UTM zone boundaries and orbit edges, and the STAC footprint is
#: the tile's bounding rectangle, not its valid-data extent — so the margin
#: covers the AOI on paper. A Mount's Bay search ranked an empty T30UUA margin
#: first, ahead of the T29UQR tile that actually holds the site.
L2A_NODATA_DN = 0

#: NIR reflectance below which a pixel is taken to be water.
#:
#: An absolute cut, not a quantile. The prototype masked water as the darkest
#: 40% of NIR in the window, which has two failure modes that both push the
#: answer the wrong way. Over a window that is entirely water — the intended
#: case, since the window is meant to be tight over the site — it still
#: discards 60% of the pixels and keeps the darkest, and because red and NIR
#: both rise with haze and turbidity that biases the median *low*, making hazy
#: scenes look clear. Over a window that is entirely land it reports water
#: anyway. An absolute threshold has neither problem: water absorbs NIR almost
#: completely, so clear water sits near 0.01, turbid coastal water reaches
#: ~0.05, and vegetation and cloud are above 0.15. Raise it for a very turbid
#: AOI rather than reaching back for a quantile.
WATER_NIR_MAX = 0.10

_MGRS_FROM_ID = re.compile(r"_T(\d{2}[A-Z]{3})_")


@dataclass(frozen=True)
class DateVerdict:
    """One overpass, screened."""

    date: date
    platform: str | None
    tile: str | None
    tile_cloud_pct: float | None
    #: Median over-water red reflectance inside the screening window.
    over_water_red: float | None
    n_water_pixels: int
    #: ``clear`` | ``marginal`` | ``hazy`` | ``tile_cloudy`` | ``no_water`` | ``unreadable``
    verdict: str

    @property
    def worth_downloading(self) -> bool:
        return self.verdict in ("clear", "marginal")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["date"] = self.date.isoformat()
        return payload


def _require_stac() -> tuple[Any, Any]:
    try:
        import planetary_computer
        import pystac_client
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "Scene screening needs pystac-client and planetary-computer. "
            "Install the acquire extra:\n"
            '    pip install "oceanstream[coastal-acquire]"'
        ) from exc
    return pystac_client, planetary_computer


def open_catalog() -> Any:
    pystac_client, planetary_computer = _require_stac()
    return pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)


def mgrs_tile(item: Any) -> str | None:
    """MGRS tile as ``T29SMC``, from STAC properties or the item id."""
    tile = item.properties.get("s2:mgrs_tile")
    if not tile:
        match = _MGRS_FROM_ID.search(item.id)
        tile = match.group(1) if match else None
    if not tile:
        return None
    tile = str(tile).upper()
    return tile if tile.startswith("T") else f"T{tile}"


def _reflectance_offset(item: Any) -> float:
    try:
        baseline = float(item.properties.get("s2:processing_baseline", 0))
    except (TypeError, ValueError):
        baseline = 0.0
    return BOA_ADD_OFFSET if baseline >= BASELINE_WITH_OFFSET else 0.0


def measure_over_water_red(
    item: Any,
    bbox: BBox,
    *,
    decimate: int = 4,
    red_asset: str = "B04",
    nir_asset: str = "B08",
    water_nir_max: float = WATER_NIR_MAX,
) -> tuple[float, int]:
    """Median red reflectance over water inside ``bbox``.

    Returns ``(median_red, n_water_pixels)``; ``(nan, n)`` when there is not
    enough water to measure. Reads are decimated hard — a median does not need
    full resolution, and this runs across a whole date range.

    Water is selected by an absolute NIR cut; see :data:`WATER_NIR_MAX` for why
    that is not a quantile.
    """
    import rasterio
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    offset = _reflectance_offset(item)

    def read(href: str) -> np.ndarray | None:
        with rasterio.open(href) as src:
            left, bottom, right, top = transform_bounds(
                "EPSG:4326", src.crs, *bbox, densify_pts=21
            )
            # A search by AOI footprint routinely returns a neighbouring tile
            # that does not actually cover the screening window. That is a
            # normal result, not an error, but rasterio raises out of the read
            # rather than the window construction, so check the overlap first.
            src_left, src_bottom, src_right, src_top = src.bounds
            if left >= src_right or right <= src_left or bottom >= src_top or top <= src_bottom:
                return None
            try:
                window = (
                    from_bounds(left, bottom, right, top, transform=src.transform)
                    .round_offsets()
                    .round_lengths()
                    .intersection(rasterio.windows.Window(0, 0, src.width, src.height))
                )
            except (ValueError, rasterio.errors.WindowError):
                return None
            if window.height <= 0 or window.width <= 0:
                return None
            out_h = max(1, int(window.height // decimate))
            out_w = max(1, int(window.width // decimate))
            block = src.read(1, window=window, out_shape=(out_h, out_w))
            return np.asarray(block, dtype=np.float32)

    red = read(item.assets[red_asset].href)
    nir = read(item.assets[nir_asset].href)
    if red is None or nir is None or red.size < 100 or red.shape != nir.shape:
        return float("nan"), 0

    # Drop the nodata sentinel before the offset, not after. See L2A_NODATA_DN.
    # Negative reflectance in real pixels is left alone: atmospheric correction
    # over dark water legitimately overshoots slightly below zero, and clamping
    # that away would bias the median up.
    observed = (red > L2A_NODATA_DN) & (nir > L2A_NODATA_DN)
    red = np.where(observed, (red + offset) / QUANTIFICATION_VALUE, np.nan)
    nir = np.where(observed, (nir + offset) / QUANTIFICATION_VALUE, np.nan)

    water = np.isfinite(nir) & (nir <= water_nir_max) & np.isfinite(red)
    n_water = int(water.sum())
    if n_water < MIN_WATER_PIXELS:
        return float("nan"), n_water
    return float(np.median(red[water])), n_water


def _coverage_fraction(item: Any, bbox: BBox) -> float:
    """Fraction of ``bbox`` that the item's footprint covers, from STAC metadata.

    A coastal AOI on a UTM zone boundary is returned by both neighbouring
    tiles, and one of them may clip it to a sliver. Ranking those two by cloud
    cover alone picks on a criterion that has nothing to do with whether the
    scene contains the site: the Summer Isles AOI straddles zones 29 and 30,
    and the lower-cloud T29VPE covers ~4% of the window that T30VUK covers in
    full. Comparing footprints costs nothing — it is metadata already in hand,
    no raster read.
    """
    item_bbox = getattr(item, "bbox", None)
    if not item_bbox or len(item_bbox) < 4:
        return 1.0  # Unknown footprint: do not penalise, let cloud decide.
    west, south, east, north = bbox
    area = (east - west) * (north - south)
    if area <= 0:
        return 0.0
    overlap_w = max(0.0, min(east, item_bbox[2]) - max(west, item_bbox[0]))
    overlap_h = max(0.0, min(north, item_bbox[3]) - max(south, item_bbox[1]))
    return float(overlap_w * overlap_h) / float(area)


def _classify(red: float, n_water: int, clear_max: float, hazy_min: float) -> str:
    if n_water < MIN_WATER_PIXELS:
        return "no_water"
    if not np.isfinite(red):
        return "unreadable"
    if red <= clear_max:
        return "clear"
    if red >= hazy_min:
        return "hazy"
    return "marginal"


def screen_dates(
    bbox: BBox,
    start: date | str,
    end: date | str,
    *,
    max_tile_cloud_pct: float = 60.0,
    clear_max: float = CLEAR_MAX,
    hazy_min: float = HAZY_MIN,
    water_nir_max: float = WATER_NIR_MAX,
    collection: str = "sentinel-2-l2a",
    limit: int | None = None,
) -> list[DateVerdict]:
    """Screen every Sentinel-2 overpass of ``bbox`` between two dates.

    ``bbox`` should be a *tight* window over the water of interest, not the
    whole processing extent. The point of the measurement is that it is local;
    widening it back out to tile scale reproduces the metric it replaces.

    Scenes above ``max_tile_cloud_pct`` are recorded as ``tile_cloudy`` without
    a read. That is a cost control, not a judgement — a heavily clouded tile
    can still be clear over the AOI, so raise the limit when screening a
    persistently cloudy region rather than concluding there are no scenes.
    """
    catalog = open_catalog()
    search = catalog.search(
        collections=[collection],
        bbox=list(bbox),
        datetime=f"{date.fromisoformat(str(start)).isoformat()}/"
        f"{date.fromisoformat(str(end)).isoformat()}",
    )
    items = list(search.items())
    logger.info("screening %d STAC item(s) over %s", len(items), bbox)

    by_date: dict[date, list[Any]] = {}
    for item in items:
        stamp = item.datetime.date() if item.datetime else None
        if stamp is not None:
            by_date.setdefault(stamp, []).append(item)

    verdicts: list[DateVerdict] = []
    for stamp in sorted(by_date):
        if limit is not None and len(verdicts) >= limit:
            break
        # One overpass returns several items when the AOI straddles tiles.
        # Coverage first, cloud second: a clearer tile that barely clips the
        # window is the wrong scene, not the better one.
        best = max(
            by_date[stamp],
            key=lambda it: (
                round(_coverage_fraction(it, bbox), 3),
                -float(it.properties.get("eo:cloud_cover", 100.0)),
            ),
        )
        tile_cloud = float(best.properties.get("eo:cloud_cover", 100.0))
        platform = best.properties.get("platform")
        tile = mgrs_tile(best)

        if tile_cloud > max_tile_cloud_pct:
            verdicts.append(
                DateVerdict(stamp, platform, tile, tile_cloud, None, 0, "tile_cloudy")
            )
            continue
        try:
            red, n_water = measure_over_water_red(best, bbox, water_nir_max=water_nir_max)
        except Exception as exc:  # noqa: BLE001 - one unreadable scene is not fatal
            logger.warning("%s unreadable: %s", stamp, exc)
            verdicts.append(
                DateVerdict(stamp, platform, tile, tile_cloud, None, 0, "unreadable")
            )
            continue
        verdicts.append(
            DateVerdict(
                date=stamp,
                platform=platform,
                tile=tile,
                tile_cloud_pct=round(tile_cloud, 1),
                over_water_red=None if not np.isfinite(red) else round(red, 4),
                n_water_pixels=n_water,
                verdict=_classify(red, n_water, clear_max, hazy_min),
            )
        )
    return verdicts


def best_dates(
    verdicts: Sequence[DateVerdict],
    n: int = 3,
    *,
    min_coverage_fraction: float = 0.5,
) -> list[DateVerdict]:
    """The ``n`` clearest well-observed dates, clearest first.

    Ranking on reflectance alone rewards the wrong thing. A median taken over a
    handful of surviving pixels is measuring a gap in the cloud, not the site,
    and because those gaps are cloud-shadowed they read *dark* — so the least
    observed scene sorts first. At Sesimbra a date with **204** water pixels
    outranked one with **31,424** on a window whose full extent is ~31,500.

    :data:`MIN_WATER_PIXELS` cannot catch this: it is an absolute floor that
    answers "is this a measurement at all", and it has no idea how much water
    the window holds when the view is clear. That number is not knowable up
    front, but it does not need to be — screening a date range reveals it. The
    best-observed date in the set is the reference, and a scene must show
    ``min_coverage_fraction`` of it to be ranked. Set to 0.0 to rank on
    reflectance alone.
    """
    usable = [v for v in verdicts if v.worth_downloading and v.over_water_red is not None]
    reference = max((v.n_water_pixels for v in usable), default=0)
    floor = min_coverage_fraction * reference
    well_observed = [v for v in usable if v.n_water_pixels >= floor]
    # A degenerate set (every date equally sparse) should still rank rather
    # than come back empty: a thin answer beats no answer.
    ranked = well_observed or usable
    return sorted(ranked, key=lambda v: v.over_water_red or 1.0)[:n]
