"""Shared input preparation and local attenuation analysis for every entry point."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from oceanstream.coastal.aoi import AOI, BathymetryReference
from oceanstream.coastal.bathymetry.tide import TideCorrection
from oceanstream.coastal.config import AttenuationConfig, BathymetryConfig, MaskConfig, QCConfig
from oceanstream.coastal.io.rasters import RasterGrid, read_band, reproject_to_grid
from oceanstream.coastal.masks import composite_mask, deep_water_pixels
from oceanstream.coastal.optics.attenuation import calibrate_bands
from oceanstream.coastal.optics.qaa import kd_map
from oceanstream.coastal.qc.ac_uncertainty import effective_threshold_rhos
from oceanstream.coastal.qc.offset import deepwater_additive_offset
from oceanstream.coastal.scene import Scene


@dataclass
class PreparedScene:
    scene: Scene
    roles: dict[str, int]
    valid: np.ndarray
    reporting: np.ndarray
    regions: dict[str, np.ndarray]
    depth: np.ndarray
    deep_depth: np.ndarray
    depth_sigma: np.ndarray
    deep: np.ndarray
    kd_490: np.ndarray
    reflectance: dict[float, np.ndarray]
    mask_components: dict[str, np.ndarray]
    provenance: dict[str, Any]
    flags: list[str] = field(default_factory=list)


def polygon_masks(
    uri: str, grid: RasterGrid, *, require_ids: bool = False
) -> dict[str, np.ndarray]:
    import geopandas as gpd
    from rasterio.features import geometry_mask
    from rasterio.transform import Affine

    frame = gpd.read_file(uri)
    if frame.empty or frame.crs is None:
        raise ValueError(f"{uri}: polygons require a declared CRS and at least one feature.")
    frame = frame.to_crs(grid.crs)
    result = {}
    occupied = np.zeros(grid.shape, bool)
    for index, row in frame.iterrows():
        geom = row.geometry
        if (
            geom is None
            or geom.is_empty
            or not geom.is_valid
            or geom.geom_type not in {"Polygon", "MultiPolygon"}
        ):
            raise ValueError(f"{uri}: every region must be a valid polygon.")
        identifier = row.get("region_id", None)
        if require_ids and (not isinstance(identifier, str) or not identifier):
            raise ValueError(f"{uri}: calibration polygons need non-empty region_id strings.")
        key = str(identifier or index)
        if key in result:
            raise ValueError(f"{uri}: duplicate region_id {key}.")
        mask = geometry_mask(
            [geom.__geo_interface__], grid.shape, Affine(*grid.transform), invert=True
        )
        if require_ids and (occupied & mask).any():
            raise ValueError(f"{uri}: calibration regions overlap on the scene grid.")
        if not mask.any():
            raise ValueError(f"{uri}: region {key} has no pixels on the scene grid.")
        result[key] = mask
        occupied |= mask
    return result


def bbox_mask(bbox: tuple[float, float, float, float], grid: RasterGrid) -> np.ndarray:
    from rasterio.features import geometry_mask
    from rasterio.transform import Affine
    from rasterio.warp import transform_geom

    from oceanstream.coastal.stac.coastal_emit import bbox_polygon

    geometry = transform_geom("EPSG:4326", grid.crs, bbox_polygon(bbox))
    return np.asarray(geometry_mask([geometry], grid.shape, Affine(*grid.transform), invert=True))


def load_reference(
    ref: BathymetryReference | None, grid: RasterGrid
) -> tuple[np.ndarray, dict[str, Any]]:
    from oceanstream.coastal.processor import _depth_sign

    if ref is None:
        return np.full(grid.shape, np.nan, np.float32), {"available": False}
    if str(ref.uri).endswith(".zip") or "coverageId=" in str(ref.uri):
        raise ValueError(
            "Bathymetry URI is a discovery record, not a depth raster. "
            "Download/subset it to a COG first."
        )
    data, source_grid = read_band(ref.uri)
    sign, sign_source = _depth_sign(data, ref.uri)
    data = data * sign
    data = np.where(data > 0, data, np.nan)
    if ref.max_reliable_depth_m is not None:
        data = np.where(data <= ref.max_reliable_depth_m, data, np.nan)
    if not source_grid.matches(grid):
        data = reproject_to_grid(data, grid, src_grid=source_grid)
    return data.astype(np.float32), {
        "available": True,
        "uri": ref.uri,
        "source_grid": source_grid.to_dict(),
        "resolution_m": ref.resolution_m or source_grid.pixel_size_m,
        "survey_year": ref.survey_year,
        "vertical_datum": ref.vertical_datum,
        "attribution": ref.attribution,
        "sign_source": sign_source,
        "uncertainty_m": ref.uncertainty_m,
        "provenance": ref.provenance,
        "max_reliable_depth_m": ref.max_reliable_depth_m,
    }


def prepare_scene(
    aoi: AOI,
    scene: Scene,
    *,
    mask_config: MaskConfig | None = None,
    bathymetry_config: BathymetryConfig | None = None,
    tide: TideCorrection | None = None,
) -> PreparedScene:
    masks = mask_config or MaskConfig()
    bathy = bathymetry_config or BathymetryConfig()
    if scene.rhos is None or scene.validate():
        raise ValueError("Scene requires matching rhos/Rrs and the sensor's required bands.")
    roles = scene.sensor.resolve_roles(scene.wavelengths_nm)
    valid, components = composite_mask(
        blue=scene.rhos[roles["blue"]],
        red=scene.rhos[roles["red"]],
        nir=scene.rhos[roles["nir"]],
        swir=scene.rhos[roles["swir1"]] if "swir1" in roles else None,
        config=masks,
    )
    valid &= bbox_mask(aoi.processing_bbox, scene.grid)
    valid &= np.all(np.isfinite(scene.rhos), axis=0)
    if not valid.any():
        raise ValueError("The composite mask rejected every pixel in the AOI.")
    fine, fine_meta = load_reference(aoi.fine_bathymetry, scene.grid)
    coarse, coarse_meta = load_reference(aoi.coarse_bathymetry, scene.grid)
    refs = [r for r in (aoi.fine_bathymetry, aoi.coarse_bathymetry) if r is not None]
    if not refs:
        raise ValueError(
            "AOI declares no bathymetry; supply fine_bathymetry and/or coarse_bathymetry."
        )
    if len({r.vertical_datum for r in refs}) > 1:
        raise ValueError(
            "Fine and coarse bathymetry use different vertical datums; "
            "harmonize them before processing."
        )
    depth = np.where(np.isfinite(fine), fine, coarse)
    deep_depth = np.where(np.isfinite(coarse), coarse, fine)
    flags = []
    if aoi.metadata.get("regions_preregistered") is False:
        flags.append("calibration_regions_exploratory")
    if aoi.metadata.get("reporting_area_provisional") is True:
        flags.append("reporting_area_provisional")
    if any(r.provenance.get("independent_of_scene") is not True for r in refs):
        flags.append("reference_independence_unverified")
    if scene.solar_zenith_source in {"unknown", "fallback"}:
        flags.append("solar_geometry_assumed")
    if not np.isfinite(depth[valid]).any():
        raise ValueError("No bathymetry coverage over usable AOI pixels.")
    fine_sigma = aoi.fine_bathymetry.uncertainty_m if aoi.fine_bathymetry else None
    coarse_sigma = aoi.coarse_bathymetry.uncertainty_m if aoi.coarse_bathymetry else None
    sigma = np.where(
        np.isfinite(fine),
        fine_sigma if fine_sigma is not None else bathy.reference_sigma_m,
        coarse_sigma if coarse_sigma is not None else bathy.reference_sigma_m,
    ).astype(np.float32)
    if any(r.uncertainty_m is None for r in refs):
        flags.append("reference_uncertainty_assumed")
    tide_meta: dict[str, Any] = {"applied": False, "model": aoi.tide_model}
    if tide is None:
        flags.append("tide_correction_missing")
    else:
        if (
            scene.acquisition_datetime is None
            or tide.when is None
            or tide.when.utcoffset() is None
            or tide.when != scene.acquisition_datetime
        ):
            raise ValueError(
                "Tide correction requires the same timezone-aware acquisition time as the scene."
            )
        if not tide.is_independent:
            raise ValueError(
                "A fitted tide offset cannot support independent calibration or validation."
            )
        datum = tide.metadata.get("vertical_datum")
        if datum != refs[0].vertical_datum:
            raise ValueError("Tide metadata.vertical_datum must match the bathymetry datum.")
        depth, deep_depth = tide.apply(depth), tide.apply(deep_depth)
        tide_meta = {"applied": True, **tide.to_dict()}
        if tide.uncertainty_m is None:
            flags.append("tide_uncertainty_missing")
        else:
            sigma = np.sqrt(sigma**2 + tide.uncertainty_m**2)
    sigma = np.where(np.isfinite(depth), sigma, np.nan)
    requested_reporting = bbox_mask(aoi.analysis_bbox, scene.grid)
    reporting = requested_reporting & valid
    if aoi.habitat_bbox is None:
        flags.append("reporting_area_not_declared")
    lo, hi = aoi.depth_valid_range_m
    reporting &= np.isfinite(depth) & (depth >= lo) & (depth <= hi)
    if aoi.attenuation_regions_uri:
        regions = polygon_masks(aoi.attenuation_regions_uri, scene.grid, require_ids=True)
        domain = bbox_mask(aoi.processing_bbox, scene.grid)
        if any((mask & ~domain).any() for mask in regions.values()):
            raise ValueError("Calibration regions must lie within the processing area.")
    else:
        regions = {"whole_scene": valid.copy()}
        flags.append("calibration_regions_not_registered")
    kd = kd_map(scene.rrs_above, scene.wavelengths_nm, valid, scene.solar_zenith_deg, band_nm=490.0)
    reference_domain = valid.copy()
    if aoi.deepwater_reference_uri:
        patches = polygon_masks(aoi.deepwater_reference_uri, scene.grid)
        reference_domain &= np.logical_or.reduce(list(patches.values()))
    else:
        flags.append("reference_regions_not_registered")
    # Apply the declared domain BEFORE the capped random draw. Sampling the
    # whole scene first can discard nearly all of a small, well-covered patch.
    deep_candidates = (
        reference_domain
        & (deep_depth > masks.deepwater_min_depth_m)
        & (deep_depth * kd > masks.deepwater_min_optical_depth)
    )
    deep = deep_water_pixels(reference_domain, deep_depth, kd, masks)
    provenance = {
        "fine": fine_meta,
        "coarse": coarse_meta,
        "tide": tide_meta,
        "coverage": {
            "valid_water_pixels": int(valid.sum()),
            "reporting_pixels": int(reporting.sum()),
            "requested_reporting_pixels_on_grid": int(requested_reporting.sum()),
            "reporting_fraction_on_grid": float(reporting.sum() / requested_reporting.sum())
            if requested_reporting.any()
            else 0.0,
            "deep_pixels": int(deep.sum()),
            "deep_candidates_before_sampling": int(deep_candidates.sum()),
            "reference_domain_water_pixels": int(reference_domain.sum()),
            "reference_fraction_of_water": float(np.isfinite(depth[valid]).mean()),
            "deep_depth_quantiles_m": np.quantile(deep_depth[deep], [0.05, 0.5, 0.95]).tolist()
            if deep.any()
            else [],
            "calibration_regions": {
                key: {
                    "pixels": int((region & valid).sum()),
                    "reference_pixels": int((region & valid & np.isfinite(depth)).sum()),
                }
                for key, region in regions.items()
            },
        },
        "reference_depth_is_independent_of_imagery": all(
            r.provenance.get("independent_of_scene") is True for r in refs
        ),
    }
    reflectance = {
        float(wl): scene.rhos[i] for i, wl in enumerate(scene.wavelengths_nm) if wl < 1000
    }
    return PreparedScene(
        scene,
        roles,
        valid,
        reporting,
        regions,
        depth,
        deep_depth,
        sigma,
        deep,
        kd,
        reflectance,
        components,
        provenance,
        flags,
    )


def analyze_attenuation(
    prepared: PreparedScene,
    *,
    config: AttenuationConfig | None = None,
    qc_config: QCConfig | None = None,
) -> dict[str, dict[str, Any]]:
    qc = qc_config or QCConfig()
    # Include SWIR in contamination assessment, even though it is not fitted.
    if prepared.scene.rhos is None:
        raise ValueError("Surface reflectance is required for reference assessment.")
    if prepared.scene.rhos is not None:
        all_bands = {
            float(w): prepared.scene.rhos[i] for i, w in enumerate(prepared.scene.wavelengths_nm)
        }
        offset = deepwater_additive_offset(all_bands, prepared.deep, config=qc)
    results = {}
    for key, region in prepared.regions.items():
        noise = effective_threshold_rhos(
            prepared.reflectance,
            prepared.depth,
            prepared.valid & region,
            pixel_size_m=prepared.scene.grid.pixel_size_m,
            config=qc,
        )
        epsilon = noise.get("effective_threshold_rhos")
        epsilon_value = (
            float(epsilon)
            if isinstance(epsilon, (int, float)) and np.isfinite(epsilon) and epsilon > 0
            else None
        )
        bands = calibrate_bands(
            prepared.reflectance,
            prepared.depth,
            prepared.valid & region,
            prepared.deep,
            config=config,
            epsilon_rhos=epsilon_value,
            solar_zenith_deg=prepared.scene.solar_zenith_deg,
        )
        results[key] = {
            "calibrations": bands,
            "noise": noise,
            "epsilon": epsilon_value,
            "offset": offset,
        }
    return results


def reference_split(
    prepared: PreparedScene, config: BathymetryConfig
) -> tuple[np.ndarray, np.ndarray]:
    """Split on native reference blocks and exclude interpolation boundaries.

    Reproject a checkerboard with bilinear weights. Only target pixels whose
    contributing reference cells are ALL in one partition may be scored.
    """
    train = np.zeros(prepared.scene.shape, bool)
    test = np.zeros_like(train)
    # Coarse first; valid fine coverage takes precedence, just as in depth.
    for name in ("coarse", "fine"):
        metadata = prepared.provenance[name]
        if not metadata.get("available"):
            continue
        source = metadata["source_grid"]
        grid = RasterGrid(**{key: source[key] for key in ("width", "height", "transform", "crs")})
        size = max(
            2,
            int(
                np.ceil(
                    config.checkerboard_block_px
                    * prepared.scene.grid.pixel_size_m
                    / grid.pixel_size_m
                )
            ),
        )
        rows, cols = np.indices(grid.shape)
        values = (((rows // size) + (cols // size)) % 2 == 0).astype(np.float32)
        partition = reproject_to_grid(values, prepared.scene.grid, src_grid=grid)
        # Use this reference's actual coverage, rather than its rectangular grid.
        ref = BathymetryReference(
            uri=metadata["uri"], max_reliable_depth_m=metadata["max_reliable_depth_m"]
        )
        coverage, _ = load_reference(ref, prepared.scene.grid)
        covered = np.isfinite(coverage)
        train[covered] = partition[covered] > 0.999
        test[covered] = partition[covered] < 0.001
    return train & prepared.valid, test & prepared.valid


def attenuation_verdict(
    prepared: PreparedScene,
    analysis: dict[str, Any],
    *,
    uncertainty: Any,
    qc_config: QCConfig | None = None,
) -> dict[str, Any]:
    """Acceptance checks shared by the processor and comparison harness."""
    from oceanstream.coastal.qc.floors import scene_floor_verdict
    from oceanstream.coastal.qc.verdicts import verdict

    floor = scene_floor_verdict(
        analysis["calibrations"], prepared.scene.solar_zenith_deg, qc_config
    )
    flags = list(prepared.flags) + list(analysis["offset"].qa_flags)
    if prepared.scene.sensor.name != "sentinel2":
        flags.append("sensor_experimental")
    if not prepared.reporting.any():
        flags.append("no_reporting_coverage")
    if not floor["passed"]:
        flags += floor["flags"] or ["attenuation_not_assessable"]
    if analysis["epsilon"] is None:
        flags.append("reflectance_noise_unmeasured")
    if uncertainty.empirical_relative_sigma is None:
        flags.append("attenuation_model_uncertainty_missing")
    return verdict(flags, floors=floor)
