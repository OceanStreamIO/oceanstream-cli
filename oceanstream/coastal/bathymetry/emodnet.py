"""EMODnet Bathymetry — discovering and preparing a depth grid for an AOI.

Onboarding a new AOI should be one call, not an afternoon. The prototype found
its depth grid by hand: someone noticed the 115 m EMODnet DTM was unusable at
Sesimbra (the whole dive area falls in four cells straddling a cliffed shoreline,
so the 7–8 m stratum read as land and the 15–16 m stratum read ~2.5 m), went
looking for a finer product, found ``HR_Lidar_Sul`` at 1/128 arc-minute, and
hard-coded its download URL. That took depth MAE from 13.35 m at 30/60 diver-
quadrat coverage to 1.77 m at 60/60 — a difference between a usable retrieval and
an unusable one, discovered by luck. This module makes it a query.

Two grids, two jobs
-------------------
An AOI generally needs both. The HR LiDAR is what the retrieval uses, but
bathymetric LiDAR stops returning around 29 m at Sesimbra, so optically deep
water for the QAA reference has to come from the coarse DTM that reaches the
shelf. Hence :class:`~oceanstream.coastal.aoi.AOI` carrying ``fine_bathymetry``
and ``coarse_bathymetry`` separately rather than one "bathymetry" field.

What the metadata is for
------------------------
``survey_year`` is not decoration. The Portuguese HR tiles were surveyed in 2011,
fifteen years before the 2026 campaigns. That is defensible over rocky reef and
indefensible over mobile sediment, and which one applies decides whether depth
error can carry a validation claim at all. So discovery returns the survey year
and it lands on :class:`~oceanstream.coastal.aoi.BathymetryReference`.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from oceanstream.coastal.aoi import AOI, BathymetryReference

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]

WFS_URL = "https://ows.emodnet-bathymetry.eu/wfs"
WMS_URL = "https://ows.emodnet-bathymetry.eu/wms"
HR_LAYER = "emodnet:hr_bathymetry_area"

#: Metres per arc-minute of latitude, for converting EMODnet's resolution
#: denominator into something comparable with a sensor GSD.
_M_PER_ARCMIN = 1852.0

#: Depths at or above the vertical datum are land or intertidal. Positive-down,
#: so "at or above the datum" is ``<= 0``.
MIN_VALID_DEPTH_M = 0.0

DEFAULT_ATTRIBUTION = "EMODnet Bathymetry. Not for navigation."

#: EMODnet documents ``elevation`` as positive up, but not every contributing
#: hydrographic office submits it that way, and the metadata does not record
#: which was meant: the Sesimbra and Lough Swilly grids carry byte-identical
#: variable attributes (``SDN:P01::HGHTALAT``, "sea-floor height above LAT")
#: while one is positive up and the other positive down. So the convention is
#: measured from the data. A sea-floor *height* grid must have area below the
#: datum — that is what a bathymetric survey is — so a grid with none is depth.
#: Between the two thresholds the answer is not clear enough to guess.
_ELEVATION_MIN_BELOW_FRACTION = 0.10
_DEPTH_MAX_BELOW_FRACTION = 0.01

#: EMODnet does not publish a machine-readable seabed-stability field, so the
#: caller must assert it. Getting it wrong changes what the product may claim,
#: which is why there is no default.
_STABILITY_NOTE = (
    "seabed_stability is not in the EMODnet metadata and must be asserted by the "
    "caller: 'stable_rock' where the substrate does not move between survey and "
    "imagery, 'mobile_sediment' where it does. It decides whether depth error can "
    "carry a validation claim."
)


def _require_requests() -> Any:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "EMODnet discovery needs requests. Install the acquire extra:\n"
            '    pip install "oceanstream[coastal-acquire]"'
        ) from exc
    return requests


@dataclass(frozen=True)
class HRArea:
    """One EMODnet high-resolution coverage polygon."""

    identifier: str
    #: Denominator of the grid spacing in arc-minutes: 128 means 1/128'.
    resolution_arcmin_denom: int | None
    download_url: str | None
    edmo_id: int | None = None
    provider: str | None = None
    survey_year: int | None = None
    product_year: int | None = None
    metadata_url: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def resolution_m(self) -> float | None:
        """Approximate north–south grid spacing in metres."""
        if not self.resolution_arcmin_denom:
            return None
        return _M_PER_ARCMIN / float(self.resolution_arcmin_denom)

    def to_reference(
        self,
        uri: str,
        seabed_stability: str | None = None,
        max_reliable_depth_m: float | None = None,
    ) -> BathymetryReference:
        """Turn a discovered area into the AOI-level reference record."""
        return BathymetryReference(
            uri=uri,
            resolution_m=self.resolution_m,
            survey_year=self.survey_year,
            product_year=self.product_year,
            seabed_stability=seabed_stability,  # type: ignore[arg-type]
            vertical_datum="LAT",
            max_reliable_depth_m=max_reliable_depth_m,
            attribution=(
                f"EMODnet Bathymetry HR-DTM {self.identifier}"
                + (f" (EDMO {self.edmo_id})" if self.edmo_id else "")
                + (f", {self.provider}" if self.provider else "")
                + ". Not for navigation."
            ),
            provenance={
                **asdict(self),
                "stability_note": _STABILITY_NOTE,
            },
        )


def _first(properties: dict[str, Any], *keys: str) -> Any:
    """EMODnet's WFS field names are not stable across layer revisions.

    Matching is case-insensitive because the same field has appeared as
    ``download_url``, ``downloadUrl`` and ``Download_URL`` across revisions,
    and a missed match degrades an HR area into an undownloadable one.
    """
    folded = {str(k).casefold(): v for k, v in properties.items()}
    for key in keys:
        value = folded.get(key.casefold())
        if value not in (None, ""):
            return value
    return None


def _to_int(value: Any) -> int | None:
    """First whole number in a value, or None.

    Deliberately forgiving: resolution arrives as ``128``, ``"128"``, ``128.0``
    or ``"1/128"``, and survey year as ``"2011"`` or ``"2011-06-01"``. Note the
    ``1/N`` case — the denominator is what matters, so the leading ``1/`` is
    dropped by an explicit split rather than by ``lstrip``, which would also eat
    the leading ``1`` of ``128`` and report a 66 m grid as if it were 14 m.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text.startswith("1/"):
        text = text[2:]
    match = re.search(r"\d+", text)
    if match is None:
        return None
    try:
        return int(match.group())
    except ValueError:
        return None


def _area_from_properties(properties: dict[str, Any]) -> HRArea:
    return HRArea(
        identifier=str(
            _first(properties, "identifier", "name", "dataset_name") or "unknown"
        ),
        resolution_arcmin_denom=_to_int(
            _first(properties, "resolution", "grid_resolution", "cell_size")
        ),
        download_url=_first(properties, "download_url", "downloadurl", "url"),
        edmo_id=_to_int(_first(properties, "edmo_id", "edmo", "edmo_code")),
        provider=_first(properties, "provider", "organisation", "originator"),
        survey_year=_to_int(_first(properties, "survey_year", "year", "survey_date")),
        # "release" is what the live service actually emits; the others are
        # historical aliases. See _SERVICE_SCHEMA_NOTE.
        product_year=_to_int(_first(properties, "release", "product_year", "dtm_year")),
        metadata_url=_first(properties, "metadata_url", "metadata"),
        properties=dict(properties),
    )


#: Fields the live ``emodnet:hr_bathymetry_area`` layer emits, captured
#: 2026-09-10. Notably absent: ``provider`` and ``survey_year``. The survey
#: date is often encoded in ``identifier`` (``BGS_2011_2_FirthOfLorn``,
#: ``201203-Atlantic_Gulf of Cadiz``) but not in a parseable, consistent
#: position, so it is left ``None`` rather than guessed — a wrong survey year
#: feeds :attr:`AOI.depth_validation_is_primary` and would license a
#: validation claim the data cannot support.
_SERVICE_FIELDS = ("download_url", "edmo_id", "identifier", "metadata_url", "release", "resolution")


def _dedupe(areas: list[HRArea]) -> list[HRArea]:
    """Collapse the one-feature-per-polygon-part responses into one per dataset.

    A multi-part coverage comes back once per part — Lough Swilly/Foyle returns
    six identical records for one 7 m dataset. Left in, every count this module
    reports is inflated and "18 HR areas cover Donegal" means "three do".
    """
    seen: dict[tuple[str, int | None], HRArea] = {}
    for a in areas:
        seen.setdefault((a.identifier, a.resolution_arcmin_denom), a)
    return list(seen.values())


def discover_hr_areas(bbox: BBox, timeout_s: float = 60.0) -> list[HRArea]:
    """High-resolution coverage intersecting ``bbox``, finest first.

    A WFS ``GetFeature`` rather than the prototype's single-point WMS
    ``GetFeatureInfo``: a point query tells you what covers one pixel, and an
    AOI that straddles two HR tiles needs to know about both.

    Axis order
    ----------
    WFS 1.0.0, deliberately. In 1.0.0 an ``EPSG:4326`` bbox is longitude-first
    by specification, with no room for interpretation. 1.1.0 mandates
    *latitude*-first, and the EMODnet server does not honour that — a
    spec-correct 1.1.0 request returns ``200 OK`` with **zero features**
    instead of an error. That is the worst possible failure here: the caller
    concludes there is no HR coverage and falls back to the 115 m DTM, which
    is precisely the outcome this module exists to prevent (see the module
    docstring — the coarse grid put Sesimbra's 15 m stratum at ~2.5 m and took
    depth MAE from 1.77 m to 13.35 m).

    If the response is empty we retry with the opposite axis order. An empty
    answer is a legitimate result — Galicia genuinely has no HR coverage — so
    the retry cannot be skipped on the assumption that empty means broken. But
    if the *retry* is what finds coverage, the server's convention has changed
    under us, and that is logged as a warning rather than silently absorbed.
    """
    requests = _require_requests()
    west, south, east, north = bbox

    def _query(bbox_param: str, version: str) -> list[HRArea]:
        params = {
            "service": "WFS",
            "version": version,
            "request": "GetFeature",
            "typeName": HR_LAYER,
            "outputFormat": "application/json",
            "srsName": "EPSG:4326",
            "bbox": bbox_param,
        }
        response = requests.get(WFS_URL, params=params, timeout=timeout_s)
        response.raise_for_status()
        features = response.json().get("features", [])
        return [_area_from_properties(f.get("properties", {})) for f in features]

    areas = _query(f"{west},{south},{east},{north}", "1.0.0")
    if not areas:
        swapped = _query(f"{south},{west},{north},{east},EPSG:4326", "1.1.0")
        if swapped:
            logger.warning(
                "EMODnet WFS returned coverage only for a latitude-first bbox. The "
                "server's axis-order convention has changed; discover_hr_areas() "
                "should be switched to WFS 1.1.0 lat-first."
            )
            areas = swapped

    return sorted(
        _dedupe(areas),
        key=lambda a: -(a.resolution_arcmin_denom or 0),
    )


def discover_hr_at_point(lon: float, lat: float, timeout_s: float = 60.0) -> HRArea | None:
    """Point-query fallback, for when the WFS layer is unavailable."""
    requests = _require_requests()
    d = 0.02
    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetFeatureInfo",
        "layers": HR_LAYER,
        "query_layers": HR_LAYER,
        "srs": "EPSG:4326",
        "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
        "width": 101,
        "height": 101,
        "x": 50,
        "y": 50,
        "info_format": "application/json",
    }
    response = requests.get(WMS_URL, params=params, timeout=timeout_s)
    response.raise_for_status()
    features = response.json().get("features", [])
    return _area_from_properties(features[0]["properties"]) if features else None


def select_finest(areas: list[HRArea]) -> HRArea:
    """The finest usable area — one that is actually downloadable."""
    downloadable = [a for a in areas if a.download_url]
    if not downloadable:
        raise ValueError(
            "No EMODnet HR area with a download URL covers this AOI. "
            f"Found {len(areas)} coverage polygon(s) without one. Fall back to the "
            "standard EMODnet DTM, and check whether its cell size resolves the "
            "depth strata you intend to report on."
        )
    return downloadable[0]


def discover_for_aoi(aoi: AOI, timeout_s: float = 60.0) -> list[HRArea]:
    """Coverage over an AOI's processing extent, finest first."""
    return discover_hr_areas(aoi.processing_bbox, timeout_s=timeout_s)


def download_area(area: HRArea, dest_dir: str | Path, timeout_s: float = 900.0) -> Path:
    """Download an HR area archive. Returns the local path.

    Skips an existing non-empty file, because these archives run to hundreds of
    megabytes and re-downloading one is never the intent.
    """
    requests = _require_requests()
    if not area.download_url:
        raise ValueError(f"HR area {area.identifier} carries no download URL.")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / Path(area.download_url).name
    if target.exists() and target.stat().st_size > 0:
        logger.info("%s already downloaded (%d bytes)", target.name, target.stat().st_size)
        return target
    logger.info("downloading %s -> %s", area.download_url, target)
    with requests.get(area.download_url, stream=True, timeout=timeout_s) as response:
        response.raise_for_status()
        tmp = target.with_suffix(target.suffix + ".part")
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                handle.write(chunk)
        tmp.replace(target)
    return target


def extract_netcdf(archive: str | Path, dest_dir: str | Path | None = None) -> Path:
    """Pull the NetCDF grid out of an EMODnet ``.emo.zip``. Returns its path.

    The archive holds three members: the NetCDF, a far larger ``.emo`` in
    EMODnet's own format, and a zipped metadata record. Only the first is wanted,
    and the ``.emo`` is big enough — 307 MB against 14 MB for Lough Swilly — that
    extracting the archive wholesale is worth avoiding.

    Caches beside the archive and reuses an existing extraction.
    """
    archive = Path(archive)
    dest_dir = Path(dest_dir) if dest_dir is not None else archive.parent
    with zipfile.ZipFile(archive) as bundle:
        members = [n for n in bundle.namelist() if n.lower().endswith(".nc")]
        if not members:
            raise ValueError(
                f"{archive} contains no NetCDF member. Members: "
                f"{', '.join(bundle.namelist())}. EMODnet HR archives normally "
                "carry one .nc alongside the .emo grid."
            )
        if len(members) > 1:
            stem = archive.name.split(".")[0]
            preferred = [n for n in members if Path(n).stem == stem]
            if len(preferred) != 1:
                raise ValueError(
                    f"{archive} contains {len(members)} NetCDF members and none "
                    f"matches the archive name: {', '.join(members)}. Extract the "
                    "one you want and pass it directly."
                )
            members = preferred
        member = members[0]
        target = dest_dir / Path(member).name
        if target.exists() and target.stat().st_size > 0:
            logger.info("%s already extracted", target.name)
            return target
        dest_dir.mkdir(parents=True, exist_ok=True)
        logger.info("extracting %s from %s", member, archive.name)
        partial = target.with_suffix(target.suffix + ".part")
        with bundle.open(member) as source, partial.open("wb") as handle:
            shutil.copyfileobj(source, handle, length=8 * 1024 * 1024)
    partial.replace(target)
    return target


def _resolve_grid(source: str | Path) -> Path:
    """Accept either a downloaded archive or a bare NetCDF."""
    path = Path(source)
    return extract_netcdf(path) if zipfile.is_zipfile(path) else path


def _source_convention(
    values: np.ndarray, source: Path, override: str | None = None
) -> tuple[bool, str]:
    """Whether ``values`` are already positive down, and how we know.

    Getting this backwards is silent: negating a depth grid puts every cell above
    the datum, the land mask then removes all of them, and the run completes with
    an empty raster rather than an error.
    """
    if override is not None:
        if override not in ("up", "down"):
            raise ValueError(
                f"source_positive must be 'up' or 'down'; got {override!r}."
            )
        return override == "down", "asserted by caller"

    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError(
            f"{source} has no finite values over this bbox, so its vertical "
            "convention cannot be determined."
        )
    below = float((finite < 0).mean())
    if below <= _DEPTH_MAX_BELOW_FRACTION:
        return True, f"positive down: only {below:.3%} of cells lie below the datum"
    if below >= _ELEVATION_MIN_BELOW_FRACTION:
        return False, f"positive up: {below:.1%} of cells lie below the datum"
    raise ValueError(
        f"Cannot tell whether {source} is positive-up elevation or positive-down "
        f"depth: {below:.1%} of cells lie below the datum, between the "
        f"{_DEPTH_MAX_BELOW_FRACTION:.0%} and {_ELEVATION_MIN_BELOW_FRACTION:.0%} "
        "thresholds. Inspect the grid and pass source_positive='up' or 'down'."
    )


def subset_to_cog(
    source: str | Path,
    output_uri: str | Path,
    bbox: BBox,
    variable: str = "elevation",
    min_depth_m: float = MIN_VALID_DEPTH_M,
    area: HRArea | None = None,
    seabed_stability: str | None = None,
    source_positive: str | None = None,
    extra_tags: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Subset an EMODnet grid to ``bbox`` and write a positive-down COG.

    ``source`` may be either the ``.emo.zip`` that :func:`download_area` returns
    or a bare NetCDF; an archive is unpacked first.

    Three conversions happen here and each is a trap if skipped:

    * **Sign.** EMODnet documents elevation as positive up, but contributors do
      not all follow it and the metadata does not say which was meant, so the
      convention is measured; pass ``source_positive`` to assert it instead.
      Everything downstream works in depth, positive down.
    * **Row order.** NetCDF rows run south → north; GeoTIFF expects north →
      south. Getting this wrong produces a vertically mirrored depth grid that
      still looks plausible.
    * **Land.** Depths at or above the datum are land or intertidal and become
      NaN, so a cliff face cannot be read as a shallow stratum.

    Provenance is written both into the GeoTIFF tags and to a ``.provenance.json``
    sidecar — the tags so the record survives the file being copied somewhere the
    sidecar is not, the sidecar so it is greppable without opening a raster.
    """
    import xarray as xr
    from rasterio.transform import from_origin

    from oceanstream.coastal.io.rasters import RasterGrid, write_cog

    west, south, east, north = bbox
    source = _resolve_grid(source)
    dataset = xr.open_dataset(source)
    if variable not in dataset:
        raise KeyError(
            f"{source} has no variable {variable!r}. Available: "
            f"{sorted(str(v) for v in dataset.data_vars)}. EMODnet DTM products "
            "normally use 'elevation'."
        )
    subset = dataset[variable].sel(lat=slice(south, north), lon=slice(west, east))
    if subset.size == 0:
        raise ValueError(
            f"{source} does not cover bbox {bbox}. Check the AOI is inside the "
            f"discovered coverage polygon"
            + (f" for {area.identifier}" if area else "")
            + "."
        )

    lats = subset["lat"].values
    lons = subset["lon"].values
    raw = subset.values.astype("float32")
    positive_down, sign_evidence = _source_convention(raw, Path(source), source_positive)
    logger.info("%s: %s", Path(source).name, sign_evidence)
    depth = raw if positive_down else -raw

    if lats[0] < lats[-1]:
        depth = depth[::-1, :]
        lats = lats[::-1]

    land = np.isfinite(depth) & (depth <= min_depth_m)
    n_finite_before = int(np.isfinite(depth).sum())
    depth = np.where(land, np.nan, depth).astype("float32")

    res_lon = float(abs(lons[1] - lons[0]))
    res_lat = float(abs(lats[1] - lats[0]))
    transform = from_origin(
        float(lons.min()) - res_lon / 2,
        float(lats.max()) + res_lat / 2,
        res_lon,
        res_lat,
    )
    grid = RasterGrid(
        width=depth.shape[1],
        height=depth.shape[0],
        transform=(transform.a, transform.b, transform.c, transform.d, transform.e, transform.f),
        crs="EPSG:4326",
    )

    remaining = depth[np.isfinite(depth)]
    if remaining.size == 0:
        raise ValueError(
            f"Subsetting {Path(source).name} to {bbox} left no valid depth cells: "
            f"{n_finite_before} finite value(s) went in and the land mask "
            f"(depth <= {min_depth_m} m) removed all of them. Either the bbox "
            f"misses the survey, or the vertical convention was read wrong "
            f"({sign_evidence}) — pass source_positive to assert it."
        )
    provenance: dict[str, Any] = {
        "source": str(source),
        "variable": variable,
        "bbox": list(bbox),
        "shape": list(depth.shape),
        "resolution_m": round(grid.pixel_size_m, 2),
        "vertical_datum": "Lowest Astronomical Tide (LAT)",
        "positive": "down",
        "source_positive": "down" if positive_down else "up",
        "source_positive_evidence": sign_evidence,
        "land_mask_threshold_m": min_depth_m,
        "n_finite_before_land_mask": n_finite_before,
        "n_land_masked": int(land.sum()),
        "n_valid": int(remaining.size),
        "depth_min_m": round(float(remaining.min()), 2) if remaining.size else None,
        "depth_max_m": round(float(remaining.max()), 2) if remaining.size else None,
        "seabed_stability": seabed_stability,
        "stability_note": _STABILITY_NOTE,
    }
    if area is not None:
        provenance["emodnet_area"] = asdict(area)

    tags = {
        "source": str(source),
        "attribution": (area.identifier if area else DEFAULT_ATTRIBUTION),
        "units": "metres",
        "positive": "down",
        "vertical_datum": "Lowest Astronomical Tide (LAT), per EMODnet spec",
        "vertical_datum_caveat": (
            "NOT corrected to acquisition-time water level. Apply a tide model "
            "with oceanstream.coastal.bathymetry.tide; do not fit the offset to "
            "the depths being validated."
        ),
        "land_mask_applied": "true",
        "land_mask_threshold_m": str(min_depth_m),
        "use_constraint": "DO NOT USE FOR NAVIGATION",
    }
    if area is not None and area.survey_year:
        tags["survey_year"] = str(area.survey_year)
        tags["survey_age_caveat"] = (
            f"Surveyed {area.survey_year}. Whether that age is defensible depends "
            "on seabed stability; see the provenance sidecar."
        )
    tags.update(extra_tags or {})

    written = write_cog(output_uri, depth, grid, nodata=float("nan"), tags=tags)
    provenance["output"] = written

    output_path = Path(str(output_uri))
    if "://" not in str(output_uri):
        # Appended, not substituted: with_suffix() would give the same sidecar
        # name to depth.tif and depth.nc in one directory.
        sidecar = output_path.with_name(output_path.name + ".provenance.json")
        sidecar.write_text(json.dumps(provenance, indent=2) + "\n")
        provenance["provenance_sidecar"] = str(sidecar)

    logger.info(
        "wrote %s (%dx%d @ %.1f m, %d valid px, %s-%s m)",
        written,
        grid.width,
        grid.height,
        grid.pixel_size_m,
        provenance["n_valid"],
        provenance["depth_min_m"],
        provenance["depth_max_m"],
    )
    return provenance


# -- Coarse regional DTM ----------------------------------------------------


WCS_URL = "https://ows.emodnet-bathymetry.eu/wcs"

#: The ~115 m mean-depth DTM. Too coarse to retrieve anything nearshore — that
#: is what the HR grids are for — and the only EMODnet product that reaches the
#: shelf edge, which is the one thing the optical fit needs from bathymetry.
GLOBAL_DTM_COVERAGE = "emodnet:mean"

#: Past this depth the water column is optically deep at blue-green
#: wavelengths, so a reference read here is water rather than a lit bottom.
DEEP_WATER_DEPTH_M = 50.0

#: A coarse grid reaching no deep water cannot supply a deep-water reference,
#: and supplying a bad one is worse than supplying none. Lough Swilly is the
#: worked example: its 7 m HR grid bottoms out at 34 m, the reference was read
#: off illuminated seabed, and the fit then discarded up to 80% of the scene as
#: non-positive residual and returned a confident k from the biased remnant.
MIN_DEEP_WATER_CELLS = 200


def fetch_global_dtm(
    bbox: BBox,
    dest_dir: str | Path,
    coverage: str = GLOBAL_DTM_COVERAGE,
    timeout_s: float = 300.0,
) -> Path:
    """Download the coarse EMODnet DTM over ``bbox`` as a GeoTIFF.

    ``bbox`` usually needs to be wider than the AOI. The grid's job here is to
    reach optically deep water, and a coastal AOI rarely contains any: the
    Sesimbra processing extent bottoms out around 119 m, so the prototype pushed
    its southern edge ~25 km out to the shelf break off Cape Espichel to find
    seabed past 1000 m. Widen towards deep water, not uniformly.

    Returns the path to the raw response, which
    :func:`global_dtm_to_cog` converts. An existing non-empty download is reused.
    """
    requests = _require_requests()
    west, south, east, north = bbox
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    slug = coverage.replace(":", "_")
    target = dest_dir / f"{slug}_{west:.3f}_{south:.3f}_{east:.3f}_{north:.3f}.tif"
    if target.exists() and target.stat().st_size > 0:
        logger.info("%s already downloaded (%d bytes)", target.name, target.stat().st_size)
        return target

    params = {
        "service": "WCS",
        "version": "2.0.1",
        "request": "GetCoverage",
        "coverageId": coverage,
        "subset": [f"Long({west},{east})", f"Lat({south},{north})"],
        "format": "image/tiff",
    }
    logger.info("GET %s %s over %s", WCS_URL, coverage, bbox)
    tmp = target.with_suffix(target.suffix + ".part")
    with requests.get(WCS_URL, params=params, stream=True, timeout=timeout_s) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=8 * 1024 * 1024):
                handle.write(chunk)

    # OWS services report failure as a 200 carrying an XML exception report.
    with tmp.open("rb") as handle:
        magic = handle.read(4)
    if magic not in (b"II*\x00", b"MM\x00*"):
        detail = tmp.read_text(errors="replace")[:600].strip()
        tmp.unlink()
        raise ValueError(
            f"EMODnet WCS returned {magic!r} rather than a GeoTIFF for coverage "
            f"{coverage!r} over {bbox}. The service answers with HTTP 200 and an "
            f"XML exception report on failure, so this is the error:\n{detail}"
        )
    tmp.replace(target)
    logger.info("wrote %s (%d bytes)", target, target.stat().st_size)
    return target


def global_dtm_to_cog(
    source: str | Path,
    output_uri: str | Path,
    min_depth_m: float = MIN_VALID_DEPTH_M,
    source_positive: str | None = None,
    min_deep_water_cells: int = MIN_DEEP_WATER_CELLS,
    extra_tags: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Convert a fetched coarse DTM to a positive-down depth COG.

    Same three conversions as :func:`subset_to_cog` — sign, row order, land —
    against a GeoTIFF rather than a NetCDF. Land matters more here than it does
    for an HR marine survey: the coarse DTM is a *terrain* model that happens to
    include bathymetry, so it carries hills. Roughly 18% of the Sesimbra tile is
    dry land, and its most negative depth, -488.9 m, is the Serra da Arrábida
    ridge. Left in, that reaches the deep-water sampler as very shallow water.

    Raises when the result holds fewer than ``min_deep_water_cells`` cells past
    :data:`DEEP_WATER_DEPTH_M`, because a coarse grid that never leaves the
    shelf has not done the job it was fetched for, and the failure it causes
    downstream is silent.
    """
    import rasterio

    from oceanstream.coastal.io.rasters import RasterGrid, write_cog

    source = Path(source)
    with rasterio.open(source) as handle:
        raw = handle.read(1).astype("float32")
        transform = handle.transform
        crs = handle.crs
        nodata_in = handle.nodata

    invalid = ~np.isfinite(raw)
    if nodata_in is not None:
        invalid |= raw == nodata_in
    invalid |= np.abs(raw) > 12000.0  # deeper than Challenger Deep, so a fill value
    raw = np.where(invalid, np.nan, raw)

    positive_down, sign_evidence = _source_convention(raw, source, source_positive)
    logger.info("%s: %s", source.name, sign_evidence)
    depth = raw if positive_down else -raw

    land = np.isfinite(depth) & (depth <= min_depth_m)
    n_finite_before = int(np.isfinite(depth).sum())
    depth = np.where(land, np.nan, depth).astype("float32")

    grid = RasterGrid(
        width=depth.shape[1],
        height=depth.shape[0],
        transform=(transform.a, transform.b, transform.c, transform.d, transform.e, transform.f),
        crs=str(crs) if crs else "EPSG:4326",
    )

    remaining = depth[np.isfinite(depth)]
    if remaining.size == 0:
        raise ValueError(
            f"{source.name} left no valid depth cells: {n_finite_before} finite "
            f"value(s) went in and the land mask (depth <= {min_depth_m} m) "
            f"removed all of them. Either the bbox is entirely dry, or the "
            f"vertical convention was read wrong ({sign_evidence}) — pass "
            "source_positive to assert it."
        )
    n_deep = int((remaining > DEEP_WATER_DEPTH_M).sum())
    if n_deep < min_deep_water_cells:
        raise ValueError(
            f"{source.name} holds {n_deep} cell(s) deeper than "
            f"{DEEP_WATER_DEPTH_M} m, below the {min_deep_water_cells} needed "
            f"for a deep-water reference (max depth {remaining.max():.1f} m). "
            "This grid was fetched to reach optically deep water and does not. "
            "Re-fetch with the bbox extended towards the shelf edge rather than "
            "lowering the threshold: a reference read off a lit bottom biases "
            "every k in the scene and the fit will not report it."
        )

    provenance: dict[str, Any] = {
        "source": str(source),
        "coverage": GLOBAL_DTM_COVERAGE,
        "role": "coarse",
        "shape": list(depth.shape),
        "resolution_m": round(grid.pixel_size_m, 2),
        "vertical_datum": "Lowest Astronomical Tide (LAT)",
        "positive": "down",
        "source_positive": "down" if positive_down else "up",
        "source_positive_evidence": sign_evidence,
        "land_mask_threshold_m": min_depth_m,
        "n_finite_before_land_mask": n_finite_before,
        "n_land_masked": int(land.sum()),
        "n_valid": int(remaining.size),
        "n_deep_water_cells": n_deep,
        "deep_water_threshold_m": DEEP_WATER_DEPTH_M,
        "depth_min_m": round(float(remaining.min()), 2),
        "depth_max_m": round(float(remaining.max()), 2),
    }

    tags = {
        "source": str(source),
        "attribution": DEFAULT_ATTRIBUTION,
        "units": "metres",
        "positive": "down",
        "role": "coarse",
        "vertical_datum": "Lowest Astronomical Tide (LAT), per EMODnet spec",
        "resolution_caveat": (
            "Coarse regional DTM (~115 m). For the deep-water optical reference, "
            "not for nearshore depth retrieval."
        ),
        "land_mask_applied": "true",
        "land_mask_threshold_m": str(min_depth_m),
        "use_constraint": "DO NOT USE FOR NAVIGATION",
    }
    tags.update(extra_tags or {})

    written = write_cog(output_uri, depth, grid, nodata=float("nan"), tags=tags)
    provenance["output"] = written

    output_path = Path(str(output_uri))
    if "://" not in str(output_uri):
        sidecar = output_path.with_name(output_path.name + ".provenance.json")
        sidecar.write_text(json.dumps(provenance, indent=2) + "\n")
        provenance["provenance_sidecar"] = str(sidecar)

    logger.info(
        "wrote %s (%dx%d @ %.1f m, %d valid px, %d deeper than %.0f m, %s-%s m)",
        written,
        grid.width,
        grid.height,
        grid.pixel_size_m,
        provenance["n_valid"],
        n_deep,
        DEEP_WATER_DEPTH_M,
        provenance["depth_min_m"],
        provenance["depth_max_m"],
    )
    return provenance
