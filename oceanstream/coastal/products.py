"""What a coastal run emits, and what must be said about each thing it emits.

Every product here is derived from a retrieval that can fail physically while
succeeding numerically — a ``k`` below the pure-water floor still produces a
finite ``z_max``, a collapsed Stumpf fit still produces a depth map, and a
k-means run over noise still produces clusters. So a product is never just an
array plus a path. It is an array, a path, and the statement that has to travel
with it.

Three of those statements are structural rather than advisory:

* **The QC verdict is a constructor argument, not an optional annotation.**
  :class:`ProductWriter` cannot be built without one. A scene that failed
  :func:`~oceanstream.coastal.qc.floors.scene_floor_verdict` writes its rasters
  tagged ``oceanstream:status = diagnostic_only``, because the failure is in the
  reflectance-versus-depth relationship the whole retrieval rests on and
  everything above it inherits it. Making the verdict optional would make
  "published without a verdict" indistinguishable from "published having passed".

* **rho_b is an effective quantity.** It is what the seabed would have to
  reflect to explain the observed radiance *given* the retrieved water column
  and the depth grid used. Depth error, IOP error and residual atmosphere all
  land in it. Naming it substrate albedo invites a material reading it cannot
  support, so the caveat is written into the GeoTIFF's own tags rather than a
  sidecar — provenance that survives the file being copied somewhere the
  sidecar is not.

* **Cluster indices are spectral classes.** ``spectral_class_2`` is a statement
  about reflectance. Calling it "kelp" is a statement about biology, and the
  physics cannot make it.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np

from oceanstream.coastal.io.rasters import RasterGrid, write_cog

logger = logging.getLogger(__name__)

MediaKind = Literal["raster", "json"]

#: Written into every raster and every JSON document a run emits.
STATUS_PUBLISHABLE = "publishable"
STATUS_DIAGNOSTIC = "diagnostic_only"

#: JSON keys and STAC properties are namespaced with a colon, which is the STAC
#: convention and round-trips through any JSON parser.
_TAG_NAMESPACE = "oceanstream"

#: GeoTIFF tags cannot use that convention. GDAL treats a colon in a metadata
#: key as a domain separator, so writing ``oceanstream:caveat`` and
#: ``oceanstream:status`` produces a *single* tag named ``oceanstream`` whose
#: value is the last pair written, formatted ``status=diagnostic_only``. The
#: caveat vanishes silently — and the caveat living inside the file rather than
#: beside it is the entire reason for writing tags at all. Underscores survive
#: intact; uppercase matches the GDAL convention for metadata keys.
_RASTER_TAG_PREFIX = "OCEANSTREAM_"


def _raster_tag(name: str) -> str:
    """Namespace a GeoTIFF metadata key in a form GDAL will not rewrite."""
    return f"{_RASTER_TAG_PREFIX}{name.upper().replace(':', '_').replace(' ', '_')}"


@dataclass(frozen=True)
class ProductSpec:
    """One product's identity, units and the caveat that has to accompany it."""

    key: str
    title: str
    description: str
    media_kind: MediaKind
    unit: str | None = None
    roles: tuple[str, ...] = ("data",)
    dtype: str = "float32"
    nodata: float = float("nan")
    #: True for label rasters, where percentiles are meaningless and
    #: interpolating between values would invent classes that do not exist.
    categorical: bool = False
    #: A statement that must be readable from the product itself. Written into
    #: GeoTIFF tags and into the STAC asset description.
    caveat: str | None = None

    @property
    def media_type(self) -> str:
        if self.media_kind == "raster":
            return "image/tiff; application=geotiff; profile=cloud-optimized"
        return "application/json"

    @property
    def extension(self) -> str:
        return ".tif" if self.media_kind == "raster" else ".json"


_RHO_B_CAVEAT = (
    "Effective benthic reflectance: what the seabed would have to reflect to "
    "explain the observed signal under the retrieved water column and the depth "
    "grid used. Depth error, IOP error and residual atmospheric signal are "
    "absorbed into it. It is not a material property of the substrate and must "
    "not be read as substrate albedo."
)

_CLASS_CAVEAT = (
    "Cluster indices are spectral classes ordered by brightness, not habitat "
    "labels. Attaching a habitat name to a class requires field validation that "
    "this product does not contain."
)

_SDB_CAVEAT = (
    "Satellite-derived bathymetry from the Stumpf log-ratio, calibrated against "
    "the reference depth surface named in the asset properties. Accuracy is "
    "reported as absolute error per depth stratum; an R-squared against the "
    "calibration surface would measure the fit, not the map."
)

_Z_MAX_CAVEAT = (
    "Detection limit for the substrate contrast named in the asset properties, "
    "not a general visibility limit. z_max is inverse-linear in k and only "
    "logarithmic in the contrast and noise assumptions, so the reported "
    "sensitivity span is the honest width of the claim."
)

_PAR_CAVEAT = (
    "Seabed PAR fraction extrapolates the fitted K_d beyond the depth window it "
    "was fitted over. Beyond that window it reports precision the fit does not "
    "have."
)


RASTER_PRODUCTS: dict[str, ProductSpec] = {
    "rho_b": ProductSpec(
        key="rho_b",
        title="Effective benthic reflectance",
        description=("Bottom reflectance recovered by closed-form Lee inversion, per band."),
        media_kind="raster",
        unit="dimensionless",
        roles=("data", "reflectance"),
        caveat=_RHO_B_CAVEAT,
    ),
    "sdb_depth": ProductSpec(
        key="sdb_depth",
        title="Satellite-derived bathymetry",
        description="Water depth from the Stumpf blue/green log ratio.",
        media_kind="raster",
        unit="m",
        roles=("data", "bathymetry"),
        caveat=_SDB_CAVEAT,
    ),
    "spectral_class": ProductSpec(
        key="spectral_class",
        title="Spectral class",
        description=("Brightness-ordered k-means clusters of effective benthic reflectance."),
        media_kind="raster",
        unit=None,
        roles=("data", "classification"),
        dtype="int16",
        nodata=-1.0,
        categorical=True,
        caveat=_CLASS_CAVEAT,
    ),
    "detectability_margin": ProductSpec(
        key="detectability_margin",
        title="Detectability margin",
        description=(
            "z_max minus depth. Positive where a substrate difference of the "
            "configured contrast would still be above the noise floor."
        ),
        media_kind="raster",
        unit="m",
        roles=("data", "quality"),
        caveat=_Z_MAX_CAVEAT,
    ),
    "detectable": ProductSpec(
        key="detectable",
        title="Detectable extent",
        description=(
            "1 where the seabed is shallower than z_max, 0 where it is not. "
            "Absence of a detection outside this mask is uninformative."
        ),
        media_kind="raster",
        unit=None,
        roles=("data", "quality"),
        dtype="uint8",
        nodata=255.0,
        categorical=True,
        caveat=_Z_MAX_CAVEAT,
    ),
    "seabed_par": ProductSpec(
        key="seabed_par",
        title="Seabed PAR fraction",
        description=(
            "Fraction of subsurface photosynthetically active radiation reaching "
            "the seabed, integrated 400-700 nm."
        ),
        media_kind="raster",
        unit="dimensionless",
        roles=("data",),
        caveat=_PAR_CAVEAT,
    ),
    "optical_depth": ProductSpec(
        key="optical_depth",
        title="Optical depth score",
        description=(
            "Depth times K_d(490). Above 4 the bottom is invisible; below 2 the retrieval is clean."
        ),
        media_kind="raster",
        unit="dimensionless",
        roles=("data", "quality"),
    ),
}

for _key, _unit in {
    "depth_sigma": "m",
    "sdb_depth_sigma": "m",
    "rho_b_sigma": "dimensionless",
    "seabed_par_sigma": "dimensionless",
    "detectability_margin_sigma": "m",
}.items():
    RASTER_PRODUCTS[_key] = ProductSpec(
        _key,
        _key.replace("_", " "),
        (
            "Conditional one-sigma propagated uncertainty; "
            "see the uncertainty budget and omitted model terms."
        ),
        "raster",
        unit=_unit,
        roles=("data", "quality"),
    )
RASTER_PRODUCTS["sdb_partition"] = ProductSpec(
    "sdb_partition",
    "SDB reference partition",
    "0 excluded, 1 calibration, 2 held out on native reference blocks.",
    "raster",
    dtype="uint8",
    nodata=255.0,
    categorical=True,
    roles=("metadata", "quality"),
)
RASTER_PRODUCTS["retrieval_depth"] = ProductSpec(
    "retrieval_depth",
    "Depth used for inversion",
    "Blend of SDB and independent reference, with source availability respected.",
    "raster",
    unit="m",
)
RASTER_PRODUCTS["valid_water"] = ProductSpec(
    "valid_water",
    "Valid water mask",
    "Composite water mask.",
    "raster",
    dtype="uint8",
    nodata=255.0,
    categorical=True,
    roles=("quality",),
)
RASTER_PRODUCTS["analysis_region"] = ProductSpec(
    "analysis_region",
    "Calibration region index",
    "Registered region index; zero outside local calibration regions.",
    "raster",
    dtype="int16",
    nodata=-1.0,
    categorical=True,
    roles=("quality",),
)


JSON_PRODUCTS: dict[str, ProductSpec] = {
    "attenuation": ProductSpec(
        key="attenuation",
        title="Empirical two-way attenuation k(lambda)",
        description=(
            "Per-band k fitted against reference bathymetry, with the pure-water "
            "floor, the quantile sensitivity sweep and the Lyzenga ratios."
        ),
        media_kind="json",
        unit="m-1",
        roles=("metadata", "data"),
    ),
    "detectability": ProductSpec(
        key="detectability",
        title="Per-band detection limits",
        description="z_max per band with its contrast and noise sensitivity span.",
        media_kind="json",
        roles=("metadata", "data"),
        caveat=_Z_MAX_CAVEAT,
    ),
    "iops": ProductSpec(
        key="iops",
        title="Scene-mean inherent optical properties",
        description="QAA v6 scene fit: a(lambda), b_b(lambda), K_d(lambda).",
        media_kind="json",
        unit="m-1",
        roles=("metadata",),
    ),
    "qc": ProductSpec(
        key="qc",
        title="Quality-control verdict",
        description=(
            "Pure-water floor and Lyzenga ratio gates, plus the "
            "atmospheric-correction uncertainty diagnostic."
        ),
        media_kind="json",
        roles=("metadata", "quality"),
    ),
    "report": ProductSpec(
        key="report",
        title="Run report",
        description="Effective configuration, provenance and product manifest.",
        media_kind="json",
        roles=("metadata",),
    ),
}


def get_spec(key: str) -> ProductSpec:
    """Look up a product spec by key, raster or JSON.

    A band-suffixed raster key (``rho_b_561``) resolves to its base spec, so
    per-band products share one description and one caveat rather than drifting.
    """
    if key in RASTER_PRODUCTS:
        return RASTER_PRODUCTS[key]
    if key in JSON_PRODUCTS:
        return JSON_PRODUCTS[key]
    base = key.rsplit("_", 1)[0]
    if base in RASTER_PRODUCTS:
        return RASTER_PRODUCTS[base]
    raise KeyError(
        f"Unknown coastal product {key!r}. Known rasters: "
        f"{sorted(RASTER_PRODUCTS)}; known documents: {sorted(JSON_PRODUCTS)}."
    )


@dataclass(frozen=True)
class WrittenProduct:
    """A product on disk or in object storage, with what is known about it."""

    key: str
    spec: ProductSpec
    href: str
    status: str
    stats: dict[str, Any] = field(default_factory=dict)
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def is_publishable(self) -> bool:
        return self.status == STATUS_PUBLISHABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.spec.title,
            "href": self.href,
            "media_type": self.spec.media_type,
            "unit": self.spec.unit,
            "roles": list(self.spec.roles),
            "status": self.status,
            "caveat": self.spec.caveat,
            "stats": self.stats,
            "properties": self.properties,
        }


def raster_stats(data: np.ndarray, spec: ProductSpec) -> dict[str, Any]:
    """Summary statistics, chosen to expose the ways a product fails quietly.

    ``valid_fraction`` is first because an empty product and a full one are the
    same shape and the same dtype, and the difference is otherwise only visible
    by opening it. For continuous rasters the 5th and 95th percentiles come with
    the min and max because a fitted map can collapse into a narrow band while
    keeping plausible extremes at a handful of outlier pixels.
    """
    finite = np.isfinite(data)
    n_valid = int(finite.sum())
    stats: dict[str, Any] = {
        "count": int(data.size),
        "valid": n_valid,
        "valid_fraction": round(n_valid / data.size, 6) if data.size else 0.0,
    }
    if n_valid == 0:
        return stats
    values = data[finite]
    if spec.categorical:
        classes, counts = np.unique(values.astype(np.int64), return_counts=True)
        stats["classes"] = {int(c): int(n) for c, n in zip(classes, counts, strict=True)}
        return stats
    p5, p50, p95 = (float(v) for v in np.percentile(values, [5, 50, 95]))
    stats.update(
        {
            "min": float(values.min()),
            "max": float(values.max()),
            "mean": float(values.mean()),
            "p5": p5,
            "median": p50,
            "p95": p95,
        }
    )
    return stats


class ProductWriter:
    """Writes a run's products, tagging each with the verdict it was produced under.

    Parameters
    ----------
    output_dir
        Local path or cloud URI. Passed straight through to
        :func:`~oceanstream.coastal.io.rasters.write_cog`, so ``az://``,
        ``s3://`` and ``gs://`` all work without the caller branching.
    grid
        The grid every raster must be on. Rasters off it are rejected rather
        than reprojected: a silent reprojection here would mean two products in
        one run were computed on different footprints and compared anyway.
    qc_verdict
        Output of :func:`~oceanstream.coastal.qc.floors.scene_floor_verdict`.
        Required — see the module docstring.
    provenance
        Free-form key/value pairs written into every product: sensor, AOI,
        acquisition date, library version. Values are stringified for GeoTIFF
        tags and kept as-is in JSON.
    """

    def __init__(
        self,
        output_dir: str | Path,
        grid: RasterGrid,
        qc_verdict: Mapping[str, Any],
        provenance: Mapping[str, Any] | None = None,
        *,
        product_verdicts: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        if "passed" not in qc_verdict:
            raise ValueError(
                "qc_verdict must carry a 'passed' key — pass the output of "
                "oceanstream.coastal.qc.floors.scene_floor_verdict(). Writing "
                "products without a verdict makes 'never checked' look identical "
                "to 'checked and passed'."
            )
        self.output_dir = str(output_dir).rstrip("/")
        self.grid = grid
        self.qc_verdict = dict(qc_verdict)
        self.provenance = dict(provenance or {})
        self.product_verdicts = dict(product_verdicts) if product_verdicts is not None else None
        self.status = STATUS_PUBLISHABLE if qc_verdict["passed"] else STATUS_DIAGNOSTIC
        self.products: dict[str, WrittenProduct] = {}
        if self.status == STATUS_DIAGNOSTIC:
            logger.warning(
                "Overall QC failed (%s); inspect individual product verdicts. Default status: %s",
                qc_verdict.get("summary", "no summary"),
                STATUS_DIAGNOSTIC,
            )

    # -- Paths --------------------------------------------------------------

    def href(self, key: str, spec: ProductSpec) -> str:
        return f"{self.output_dir}/{key}{spec.extension}"

    # -- Writing ------------------------------------------------------------

    def _common_tags(self, key: str, spec: ProductSpec) -> dict[str, str]:
        verdict = self.verdict_for(key)
        tags = {
            _raster_tag("product"): key,
            _raster_tag("title"): spec.title,
            _raster_tag("status"): STATUS_PUBLISHABLE if verdict["passed"] else STATUS_DIAGNOSTIC,
            _raster_tag("qc_passed"): str(bool(verdict["passed"])),
            _raster_tag("qc_summary"): str(verdict.get("summary", "")),
            _raster_tag("schema_version"): "2.0",
        }
        if spec.unit:
            tags[_raster_tag("unit")] = spec.unit
        if spec.caveat:
            tags[_raster_tag("caveat")] = spec.caveat
        for name, value in self.provenance.items():
            if _raster_tag(name) in tags:
                raise ValueError(f"Metadata cannot override reserved product field {name}.")
            tags[_raster_tag(name)] = str(value)
        return tags

    def verdict_for(self, key: str) -> dict[str, Any]:
        if self.product_verdicts is None:
            return self.qc_verdict
        spec = get_spec(key)
        verdict = self.product_verdicts.get(key, self.product_verdicts.get(spec.key))
        return (
            dict(verdict)
            if verdict is not None
            else {
                "passed": False,
                "flags": ["product_not_assessed"],
                "summary": "Product was not assessed.",
            }
        )

    def write_raster(
        self,
        key: str,
        data: np.ndarray,
        properties: Mapping[str, Any] | None = None,
    ) -> WrittenProduct:
        """Write one raster product as a COG, tagged with its caveat and verdict.

        ``key`` may be band-suffixed (``rho_b_561``); the base spec supplies the
        title, unit and caveat so per-band products cannot describe themselves
        differently from one another.
        """
        spec = get_spec(key)
        if spec.media_kind != "raster":
            raise ValueError(f"{key!r} is a {spec.media_kind} product; use write_json() for it.")
        if data.shape != self.grid.shape:
            raise ValueError(
                f"{key} has shape {data.shape} but the run grid is "
                f"{self.grid.shape}. Reproject with "
                "oceanstream.coastal.io.rasters.reproject_to_grid() before "
                "writing — products in one run must share a footprint or every "
                "per-pixel comparison between them is meaningless."
            )

        stats = raster_stats(data, spec)
        encoded = _encode(data, spec)
        tags = self._common_tags(key, spec)
        for name, value in (properties or {}).items():
            if _raster_tag(name) in tags:
                raise ValueError(f"Metadata cannot override reserved product field {name}.")
            tags[_raster_tag(name)] = str(value)

        href = write_cog(
            self.href(key, spec),
            encoded,
            self.grid,
            nodata=spec.nodata,
            tags=tags,
            dtype=spec.dtype,
        )
        verdict = self.verdict_for(key)
        product = WrittenProduct(
            key=key,
            spec=spec,
            href=href,
            status=STATUS_PUBLISHABLE if verdict["passed"] else STATUS_DIAGNOSTIC,
            stats=stats,
            properties={**dict(properties or {}), "qc": verdict},
        )
        self.products[key] = product
        return product

    def write_json(
        self,
        key: str,
        payload: Mapping[str, Any],
        properties: Mapping[str, Any] | None = None,
    ) -> WrittenProduct:
        """Write one JSON product, wrapped in the same provenance envelope."""
        spec = get_spec(key)
        if spec.media_kind != "json":
            raise ValueError(f"{key!r} is a {spec.media_kind} product; use write_raster() for it.")
        document = {
            "schema_version": "2.0",
            f"{_TAG_NAMESPACE}:product": key,
            f"{_TAG_NAMESPACE}:title": spec.title,
            f"{_TAG_NAMESPACE}:status": STATUS_PUBLISHABLE
            if self.verdict_for(key)["passed"]
            else STATUS_DIAGNOSTIC,
            f"{_TAG_NAMESPACE}:caveat": spec.caveat,
            f"{_TAG_NAMESPACE}:provenance": self.provenance,
            f"{_TAG_NAMESPACE}:qc_passed": bool(self.verdict_for(key)["passed"]),
            f"{_TAG_NAMESPACE}:qc": self.verdict_for(key),
            f"{_TAG_NAMESPACE}:generated": datetime.now(UTC).isoformat(),
            key: jsonable(payload),
        }
        href = write_json_document(self.href(key, spec), document)
        product = WrittenProduct(
            key=key,
            spec=spec,
            href=href,
            status=STATUS_PUBLISHABLE if self.verdict_for(key)["passed"] else STATUS_DIAGNOSTIC,
            properties={**dict(properties or {}), "qc": self.verdict_for(key)},
        )
        self.products[key] = product
        return product

    # -- Manifest -----------------------------------------------------------

    def manifest(self) -> dict[str, Any]:
        """Everything written, plus the verdict it was written under."""
        return {
            "status": self.status,
            "qc_passed": bool(self.qc_verdict["passed"]),
            "qc_summary": self.qc_verdict.get("summary", ""),
            "output_dir": self.output_dir,
            "grid": self.grid.to_dict(),
            "provenance": jsonable(self.provenance),
            "products": {k: p.to_dict() for k, p in sorted(self.products.items())},
        }


def _encode(data: np.ndarray, spec: ProductSpec) -> np.ndarray:
    """Cast to the spec's dtype, substituting nodata for NaN in integer rasters.

    Integer dtypes have no NaN, so an unmasked cast turns every invalid pixel
    into whatever the platform's float-to-int conversion produces — commonly 0,
    which for a class raster is a valid class.
    """
    if np.issubdtype(np.dtype(spec.dtype), np.floating):
        return data.astype(spec.dtype)
    filled = np.where(np.isfinite(data), data, spec.nodata)
    return filled.astype(spec.dtype)


def jsonable(obj: Any) -> Any:
    """Convert numpy scalars, arrays, paths and dataclasses into plain JSON types.

    Non-finite floats become ``None`` rather than the ``NaN`` literal that
    :mod:`json` emits by default: ``NaN`` is not valid JSON and every strict
    parser downstream rejects it, which turns a missing value into an
    unreadable file.
    """
    import dataclasses

    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, (np.floating, np.integer)):
        return jsonable(obj.item())
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return [jsonable(v) for v in obj.tolist()]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, Mapping):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return jsonable(dataclasses.asdict(obj))
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if hasattr(obj, "isoformat"):
        return str(obj.isoformat())
    return str(obj)


def write_json_document(uri: str | Path, payload: Mapping[str, Any]) -> str:
    """Write JSON locally or to object storage, atomically when local."""
    text = json.dumps(jsonable(payload), indent=2, sort_keys=False)
    uri_str = str(uri)

    from oceanstream.coastal.io.rasters import _is_cloud_uri, _upload

    if _is_cloud_uri(uri_str):
        with tempfile.TemporaryDirectory(prefix="oceanstream-coastal-") as tmpdir:
            staged = Path(tmpdir) / Path(uri_str).name
            staged.write_text(text)
            _upload(staged, uri_str)
        return uri_str

    out = Path(uri_str)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, out)
    return str(out)
