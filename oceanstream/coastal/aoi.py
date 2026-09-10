"""Area of interest — the *place*, and nothing else.

The prototype's ``SiteProfile`` (``kelp_observe/tools/lee_demo/sites.py``)
carried three unrelated things at once: geography, instrument properties
(``deglint_reference_nm``) and per-acquisition solar geometry
(``solar_zenith_fallback_s2_deg`` / ``_other_deg``). That worked while there was
one site and one sensor, and stops working the moment either varies: a solar
zenith is a property of *when a scene was taken*, not of a coastline, and a
deglint reference band is a property of *the instrument*. Phase 2 splits them
into three orthogonal objects — :class:`AOI` here, ``SensorProfile`` in
``sensors.py``, ``Scene`` in ``scene.py``.

Footprints are explicit
-----------------------
The prototype also conflated four different footprints and then reported a
statistic computed over one as if it described another — for the 27 June scene
the processing grid covered 1601 km², the flatness comparison drew on 114 km²,
and every diver quadrat fell inside a single 112 m × 117 m box. So each
footprint here has one job:

``processing_bbox``
    The grid ACOLITE is asked to produce. Deliberately extends offshore to
    reach optically deep water. **Not** an area over which to report benthic
    statistics.
``habitat_bbox``
    Where benthic claims may be made.
``deepwater_reference_uri``
    Pre-registered bottom-free patches for the IOP fit. Pre-registered because
    selecting reference water *after* seeing which choice flattens the output
    would make the whole diagnostic circular.

No paths are defaulted here. The prototype resolved everything against a
repository root, which is fine for one checkout and wrong for a library: an AOI
supplies URIs (local, ``az://``, ``s3://``) or supplies nothing.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

BBox = tuple[float, float, float, float]

#: How stable the seabed is on the timescale separating survey and imagery.
SeabedStability = Literal["stable_rock", "mobile_sediment"]


def _validate_bbox(bbox: BBox, label: str) -> None:
    west, south, east, north = bbox
    if not (west < east and south < north):
        raise ValueError(
            f"{label} must be ordered (west, south, east, north) with "
            f"west < east and south < north; got {bbox}."
        )
    if not (-180.0 <= west and east <= 180.0):
        raise ValueError(f"{label} longitudes must lie in [-180, 180]; got {bbox}.")
    if not (-90.0 <= south and north <= 90.0):
        raise ValueError(f"{label} latitudes must lie in [-90, 90]; got {bbox}.")


def _contains(outer: BBox, inner: BBox) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


@dataclass(frozen=True)
class BathymetryReference:
    """A depth grid, and — the part that matters — how old it is.

    ``survey_year`` is the survey, not the product release. The two Portuguese
    HR tiles used by the prototype both come from the same 2011 DGT/APA LiDAR
    programme but age very differently: a 15-year gap is defensible over rocky
    reef and is not over mobile sediment. That distinction decides whether depth
    error can carry a validation claim at all, so it is a field rather than a
    footnote — see :attr:`AOI.depth_validation_is_primary`.
    """

    uri: str
    #: Nominal grid spacing in metres. EMODnet 1/16 arc-minute is ~115 m;
    #: the 1/128 HR tiles are ~11 m.
    resolution_m: float | None = None
    survey_year: int | None = None
    product_year: int | None = None
    seabed_stability: SeabedStability | None = None
    #: Reference surface the depths are quoted against — "LAT" for EMODnet.
    #: Correcting to instantaneous water level is ``bathymetry.tide``'s job.
    vertical_datum: str = "LAT"
    #: Depth past which the sounding method stops returning. Bathymetric LiDAR
    #: reaches ~29 m at Sesimbra, so deep reference water for the QAA fit has
    #: to come from a coarser grid.
    max_reliable_depth_m: float | None = None
    attribution: str | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AOI:
    """A coastal area of interest.

    Only ``name`` and ``processing_bbox`` are required; everything else refines
    what may be claimed about the result.
    """

    name: str
    #: (west, south, east, north) in EPSG:4326. The ACOLITE output grid.
    processing_bbox: BBox
    label: str | None = None
    #: Where benthic claims may be made. Must sit inside ``processing_bbox``.
    #: When absent, the caller has not declared an analysis area and area-based
    #: statistics should not be reported.
    habitat_bbox: BBox | None = None
    #: Fine bathymetry (LiDAR / HR-DTM) — the depth grid the retrieval uses.
    fine_bathymetry: BathymetryReference | None = None
    #: Coarse bathymetry (EMODnet DTM) — reaches past the LiDAR cut-off, so it
    #: is what supplies optically deep water for the QAA reference.
    coarse_bathymetry: BathymetryReference | None = None
    #: Vector file (GeoJSON / GPKG) of pre-registered bottom-free patches.
    deepwater_reference_uri: str | None = None
    #: Tide model identifier for the LAT → instantaneous datum correction.
    #: ``None`` means depths stay on the reference datum and z_max carries the
    #: resulting bias — see ``bathymetry.tide``.
    tide_model: str | None = None
    #: Physically plausible depth range at this AOI, in metres. Feeds
    #: ``BathymetryConfig.depth_valid_range_m``; a retrieval outside it is a
    #: failure, not a value.
    depth_valid_range_m: tuple[float, float] = (0.0, 40.0)
    #: Identifiers this AOI carries in other systems, e.g.
    #: ``{"earthstudio": "a3d3ba9f-d30f-4f14-9df1-3f9f444e60e7"}``. An opaque
    #: mapping, so the library never needs to import those systems.
    external_ids: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("AOI.name must be a non-empty slug.")
        _validate_bbox(self.processing_bbox, "AOI.processing_bbox")
        if self.habitat_bbox is not None:
            _validate_bbox(self.habitat_bbox, "AOI.habitat_bbox")
            if not _contains(self.processing_bbox, self.habitat_bbox):
                raise ValueError(
                    f"AOI.habitat_bbox {self.habitat_bbox} is not contained in "
                    f"processing_bbox {self.processing_bbox}. The processing grid "
                    "must cover every pixel a benthic claim is made about."
                )
        lo, hi = self.depth_valid_range_m
        if not lo < hi:
            raise ValueError(
                f"AOI.depth_valid_range_m must be (min, max) with min < max; "
                f"got {self.depth_valid_range_m}."
            )

    # -- Derived geometry ---------------------------------------------------

    @property
    def display_label(self) -> str:
        return self.label or self.name

    @property
    def centroid(self) -> tuple[float, float]:
        """(lon, lat) centre of ``processing_bbox``."""
        west, south, east, north = self.processing_bbox
        return ((west + east) / 2.0, (south + north) / 2.0)

    @property
    def utm_epsg(self) -> int:
        """UTM zone EPSG for metric work (areas, distances, buffers).

        Sesimbra (-9.05 °E) resolves to 32629, the CRS the prototype hard-coded.
        Derived rather than stored so a new AOI needs no lookup.
        """
        lon, lat = self.centroid
        zone = int((lon + 180.0) // 6.0) + 1
        return (32600 if lat >= 0 else 32700) + zone

    @property
    def acolite_limit(self) -> BBox:
        """ACOLITE's ``limit=`` ordering, which is (south, west, north, east).

        Not the usual bbox order, and getting it wrong yields an empty or
        transposed subset rather than an error, so it is converted in one place.
        """
        west, south, east, north = self.processing_bbox
        return (south, west, north, east)

    @property
    def analysis_bbox(self) -> BBox:
        """The footprint benthic statistics may be reported over."""
        return self.habitat_bbox or self.processing_bbox

    @property
    def depth_validation_is_primary(self) -> bool:
        """Whether depth error can carry the validation claim at this AOI.

        False where the reference grid predates the imagery over moving seabed:
        there, depth MAE confounds retrieval error with real bathymetric change,
        and bottom reflectance has to carry the claim instead.
        """
        ref = self.fine_bathymetry or self.coarse_bathymetry
        return bool(ref is not None and ref.seabed_stability == "stable_rock")

    # -- Serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Plain-JSON view, for embedding in a run report or STAC item."""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AOI:
        """Rebuild from :meth:`to_dict` output or a hand-written JSON file."""
        data = dict(payload)
        for key in ("processing_bbox", "habitat_bbox", "depth_valid_range_m"):
            if data.get(key) is not None:
                data[key] = tuple(data[key])
        for key in ("fine_bathymetry", "coarse_bathymetry"):
            value = data.get(key)
            if isinstance(value, Mapping):
                data[key] = BathymetryReference(**value)
        known = set(cls.__dataclass_fields__)
        unknown = set(data) - known
        if unknown:
            raise ValueError(
                f"Unknown AOI fields {sorted(unknown)}. Known fields: "
                f"{sorted(known)}. Put anything else under 'metadata'."
            )
        return cls(**data)

    @classmethod
    def from_json(cls, path: str | Path) -> AOI:
        return cls.from_dict(json.loads(Path(path).read_text()))

    @classmethod
    def from_geojson(cls, path: str | Path, name: str | None = None) -> AOI:
        """Build from a GeoJSON file, taking ``processing_bbox`` from its extent.

        Convenience for the CLI (``--aoi aoi.geojson``). Only the bounding box
        is read, and ``metadata`` records that, so an irregular polygon is not
        silently treated as if its edges were honoured.
        """
        import geopandas as gpd

        source = Path(path)
        gdf = gpd.read_file(source).to_crs(4326)
        if gdf.empty:
            raise ValueError(f"No features in {source}.")
        west, south, east, north = (float(v) for v in gdf.total_bounds)
        return cls(
            name=name or source.stem,
            processing_bbox=(west, south, east, north),
            metadata={
                "source_geojson": str(source),
                "n_features": int(len(gdf)),
                "bbox_note": (
                    "Reduced from the file's geometry to its bounding box; "
                    "non-rectangular AOI edges are not honoured."
                ),
            },
        )

    @classmethod
    def from_earthstudio(cls, payload: Mapping[str, Any]) -> AOI:
        """Adapt an EarthStudio AOI record.

        Takes the already-fetched record rather than an AOI id, so the core
        library needs no HTTP client and no knowledge of EarthStudio's auth —
        fetching belongs to the ``coastal-acquire`` extra. EarthStudio is the
        system of record for AOI identity, so its UUID is preserved in
        ``external_ids`` and every product can be traced back to it.
        """
        geometry = payload.get("geometry")
        bbox = payload.get("bbox")
        if bbox is None and geometry is not None:
            bbox = _bbox_of_geojson_geometry(geometry)
        if bbox is None:
            raise ValueError(
                "EarthStudio AOI record carries neither 'bbox' nor 'geometry'; "
                "cannot derive a processing extent."
            )
        aoi_id = payload.get("id")
        name = payload.get("slug") or payload.get("name") or aoi_id
        if name is None:
            raise ValueError(
                "EarthStudio AOI record carries no 'slug', 'name' or 'id' to "
                "name the AOI with."
            )
        return cls(
            name=str(name),
            label=payload.get("name"),
            processing_bbox=(
                float(bbox[0]),
                float(bbox[1]),
                float(bbox[2]),
                float(bbox[3]),
            ),
            external_ids={"earthstudio": str(aoi_id)} if aoi_id else {},
            metadata={"earthstudio_record": dict(payload)},
        )


def _bbox_of_geojson_geometry(geometry: Mapping[str, Any]) -> BBox:
    """Bounding box of a GeoJSON geometry, without a geometry library."""

    def _coords(node: Any) -> list[tuple[float, float]]:
        if (
            isinstance(node, (list, tuple))
            and len(node) >= 2
            and all(isinstance(v, (int, float)) for v in node[:2])
        ):
            return [(float(node[0]), float(node[1]))]
        if isinstance(node, (list, tuple)):
            return [pt for child in node for pt in _coords(child)]
        return []

    if geometry.get("type") == "GeometryCollection":
        points = [
            pt
            for geom in geometry.get("geometries", [])
            for pt in _coords(geom.get("coordinates"))
        ]
    else:
        points = _coords(geometry.get("coordinates"))
    if not points:
        raise ValueError(f"No coordinates in geometry of type {geometry.get('type')!r}.")
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    return (min(lons), min(lats), max(lons), max(lats))
