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
        product_year=_to_int(_first(properties, "product_year", "dtm_year")),
        metadata_url=_first(properties, "metadata_url", "metadata"),
        properties=dict(properties),
    )


def discover_hr_areas(bbox: BBox, timeout_s: float = 60.0) -> list[HRArea]:
    """High-resolution coverage intersecting ``bbox``, finest first.

    A WFS ``GetFeature`` rather than the prototype's single-point WMS
    ``GetFeatureInfo``: a point query tells you what covers one pixel, and an
    AOI that straddles two HR tiles needs to know about both.
    """
    requests = _require_requests()
    west, south, east, north = bbox
    params = {
        "service": "WFS",
        "version": "1.1.0",
        "request": "GetFeature",
        "typeName": HR_LAYER,
        "outputFormat": "application/json",
        "srsName": "EPSG:4326",
        # WFS 1.1.0 with EPSG:4326 is latitude-first.
        "bbox": f"{south},{west},{north},{east},EPSG:4326",
    }
    response = requests.get(WFS_URL, params=params, timeout=timeout_s)
    response.raise_for_status()
    features = response.json().get("features", [])
    areas = [_area_from_properties(f.get("properties", {})) for f in features]
    return sorted(
        areas,
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


def subset_to_cog(
    source: str | Path,
    output_uri: str | Path,
    bbox: BBox,
    variable: str = "elevation",
    min_depth_m: float = MIN_VALID_DEPTH_M,
    area: HRArea | None = None,
    seabed_stability: str | None = None,
    extra_tags: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Subset an EMODnet grid to ``bbox`` and write a positive-down COG.

    Three conversions happen here and each is a trap if skipped:

    * **Sign.** EMODnet stores elevation, positive up. Everything downstream
      works in depth, positive down.
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
    depth = -subset.values.astype("float32")

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
    provenance: dict[str, Any] = {
        "source": str(source),
        "variable": variable,
        "bbox": list(bbox),
        "shape": list(depth.shape),
        "resolution_m": round(grid.pixel_size_m, 2),
        "vertical_datum": "Lowest Astronomical Tide (LAT)",
        "positive": "down",
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
