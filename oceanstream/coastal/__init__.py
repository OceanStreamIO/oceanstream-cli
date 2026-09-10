"""OceanStream Coastal Optics Library.

Physics-based retrieval of water-column and detectability products from
top-of-atmosphere-corrected multispectral imagery (Sentinel-2, Pléiades Neo,
future sensors) at coastal sites.

Products
--------
- Sub-Surface Reflectance (SSR) and IOPs (a, b_b, Kd) via Lee et al.
  1998/1999 semi-analytical inversion + QAA v6 scene-mean IOPs.
- Empirical two-way attenuation k(λ) from deep-water reference.
- Effective benthic reflectance rho_b (labelled as such, not a material
  property).
- Detectability products: z_max per band, seabed PAR, Satellite-Derived
  Bathymetry (Stumpf).
- QC verdicts (pure-water floor, Lyzenga ratio, AC uncertainty) that gate
  the "usable" bands per scene, per AOI, without ground truth.

Usage as library:
    from oceanstream.coastal import CoastalProcessor, RetrievalConfig, AOI

    config = RetrievalConfig()
    processor = CoastalProcessor(config=config, campaign_id="sesimbra_2026")
    result = processor.run(aoi=aoi, scene=scene, output_dir=out_dir)

Usage as CLI:
    oceanstream process coastal detect --aoi aoi.geojson --scene scene.json \\
        --output-dir ./out

Dependencies:
    Install with the coastal extra:
        pip install "oceanstream[coastal]"
    Optional scene-fetch helpers (Copernicus + Planetary Computer) come with
    the coastal-acquire extra:
        pip install "oceanstream[coastal-acquire]"

Scope
-----
The library is standalone: no EarthStudio import, no Prefect import, no cloud
assumption baked into the physics. Scene acquisition, orchestration, alerting
and publishing live in adjacent projects (see docs/lee_inversion/).
"""

from __future__ import annotations

# Lazy re-exports — heavy dependencies (rasterio, geopandas, scipy) load only
# when the caller reaches for a symbol that needs them. Follows the same
# pattern as oceanstream.echodata.
__all__ = [
    # Water optics primitives (Phase 1.1)
    "rrs_above_to_subsurface",
    "rrs_subsurface_to_above",
    "rhos_to_rrs",
    "a_water",
    "bb_water",
    "a_cdm_shape",
    "bbp_shape",
    # Lee inversion (Phase 1.1)
    "invert_scene",
    "forward_model",
    "optical_depth_score",
    "kb_pure_water",
    "kd_pure_water",
    "two_way_to_downwelling_factor",
    # QAA v6 (Phase 1.2)
    "SceneIOPs",
    "fit_scene_iops",
    "kd_map",
    # Masks + classification (Phase 1.3)
    "composite_mask",
    "deep_water_pixels",
    "classify_bottom",
    "ClusterResult",
    # Bathymetry (Phase 1.3–1.4)
    "StumpfFit",
    "DepthValidation",
    "stumpf_ratio",
    "fit_stumpf",
    "fit_stumpf_on_points",
    "apply_stumpf",
    "blend_stumpf",
    "terrain_classes",
    # Empirical attenuation (Phase 1.5)
    "BandCalibration",
    "deep_water_reference",
    "fit_band_attenuation",
    "calibrate_bands",
    "lyzenga_ratios",
    # Diagnostics / QC (Phase 1.6, 3.3)
    "nearest_band",
    "within_block_scatter",
    "effective_threshold_rhos",
    "invert_uncensored",
    "point_diagnostic",
    "pure_water_floor_check",
    "lyzenga_ratio_check",
    "scene_floor_verdict",
    # Detectability products (Phase 3.1–3.2)
    "BandDetectability",
    "SceneDetectability",
    "SUBSTRATE_ALBEDO",
    "substrate_contrast",
    "z_max_from_k",
    "detectability_margin",
    "detectable_mask",
    "band_detectability",
    "scene_detectability",
    "downwelling_kd",
    "interpolate_kd",
    "par_weights",
    "seabed_par_fraction",
    "euphotic_depth",
    # High-level API (Phase 1+)
    "CoastalProcessor",
    "CoastalResult",
    "RetrievalConfig",
    "MaskConfig",
    "BathymetryConfig",
    "AttenuationConfig",
    "QCConfig",
    "DetectabilityConfig",
    # Place / instrument / scene (Phase 2)
    "AOI",
    "BathymetryReference",
    "SensorProfile",
    "SENSORS",
    "get_sensor",
    "SENTINEL2",
    "PLEIADES_NEO",
    "Scene",
    # Raster IO (Phase 2.6)
    "RasterGrid",
    "read_band",
    "read_stack",
    "reproject_to_grid",
    "write_cog",
    # ACOLITE driver (Phase 2.5)
    "correct_scene",
    "run_acolite",
    "output_is_complete",
    # Bathymetry adapters (Phase 2.3–2.4)
    "HRArea",
    "discover_hr_areas",
    "discover_for_aoi",
    "select_finest",
    "subset_to_cog",
    "TideCorrection",
    "TideProvider",
    "ConstantTide",
    "NullTide",
    "correct_depth",
    "fit_offset_from_reference",
]


def __getattr__(name: str) -> object:  # noqa: PLR0911, PLR0912
    """Lazy attribute resolution — mirror of oceanstream.echodata.

    One branch per submodule, so the branch and return counts grow with the
    module list rather than with any complexity here.
    """
    # Config dataclasses
    if name in (
        "RetrievalConfig",
        "MaskConfig",
        "BathymetryConfig",
        "AttenuationConfig",
        "QCConfig",
        "DetectabilityConfig",
    ):
        from oceanstream.coastal import config as _config
        return getattr(_config, name)
    # Place / instrument / scene
    if name in ("AOI", "BathymetryReference"):
        from oceanstream.coastal import aoi as _aoi
        return getattr(_aoi, name)
    if name in ("SensorProfile", "SENSORS", "get_sensor", "SENTINEL2", "PLEIADES_NEO"):
        from oceanstream.coastal import sensors as _sensors
        return getattr(_sensors, name)
    if name == "Scene":
        from oceanstream.coastal.scene import Scene
        return Scene
    # Raster IO
    if name in (
        "RasterGrid",
        "read_band",
        "read_stack",
        "reproject_to_grid",
        "write_cog",
    ):
        from oceanstream.coastal.io import rasters as _rasters
        return getattr(_rasters, name)
    # ACOLITE driver
    if name in ("correct_scene", "run_acolite", "output_is_complete"):
        from oceanstream.coastal import acolite as _acolite
        return getattr(_acolite, name)
    # Bathymetry adapters
    if name in (
        "HRArea",
        "discover_hr_areas",
        "discover_for_aoi",
        "select_finest",
        "subset_to_cog",
    ):
        from oceanstream.coastal.bathymetry import emodnet as _emodnet
        return getattr(_emodnet, name)
    if name in (
        "TideCorrection",
        "TideProvider",
        "ConstantTide",
        "NullTide",
        "correct_depth",
        "fit_offset_from_reference",
    ):
        from oceanstream.coastal.bathymetry import tide as _tide
        return getattr(_tide, name)
    # Water optics primitives
    if name in (
        "rrs_above_to_subsurface",
        "rrs_subsurface_to_above",
        "rhos_to_rrs",
        "a_water",
        "bb_water",
        "a_cdm_shape",
        "bbp_shape",
    ):
        from oceanstream.coastal.optics import water
        return getattr(water, name)
    # QAA
    if name in ("SceneIOPs", "fit_scene_iops", "kd_map"):
        from oceanstream.coastal.optics import qaa
        return getattr(qaa, name)
    # Lee inversion
    if name in (
        "invert_scene",
        "forward_model",
        "optical_depth_score",
        "kb_pure_water",
        "kd_pure_water",
        "two_way_to_downwelling_factor",
    ):
        from oceanstream.coastal.inversion import lee
        return getattr(lee, name)
    # Masks + classification
    if name in ("composite_mask", "deep_water_pixels"):
        from oceanstream.coastal import masks
        return getattr(masks, name)
    if name in ("classify_bottom", "ClusterResult"):
        from oceanstream.coastal import classify
        return getattr(classify, name)
    # Bathymetry
    if name in (
        "StumpfFit",
        "DepthValidation",
        "stumpf_ratio",
        "fit_stumpf",
        "fit_stumpf_on_points",
        "apply_stumpf",
        "blend_stumpf",
    ):
        from oceanstream.coastal.bathymetry import stumpf
        return getattr(stumpf, name)
    if name == "terrain_classes":
        from oceanstream.coastal.bathymetry import terrain
        return terrain.terrain_classes
    # Empirical attenuation
    if name in (
        "BandCalibration",
        "deep_water_reference",
        "fit_band_attenuation",
        "calibrate_bands",
        "lyzenga_ratios",
    ):
        from oceanstream.coastal.optics import attenuation
        return getattr(attenuation, name)
    # Diagnostics / QC
    if name in (
        "nearest_band",
        "within_block_scatter",
        "effective_threshold_rhos",
    ):
        from oceanstream.coastal.qc import ac_uncertainty
        return getattr(ac_uncertainty, name)
    if name in ("invert_uncensored", "point_diagnostic"):
        from oceanstream.coastal.qc import point_diagnostic as _pd
        return getattr(_pd, name)
    if name in (
        "pure_water_floor_check",
        "lyzenga_ratio_check",
        "scene_floor_verdict",
    ):
        from oceanstream.coastal.qc import floors
        return getattr(floors, name)
    # Detectability products
    if name in (
        "BandDetectability",
        "SceneDetectability",
        "SUBSTRATE_ALBEDO",
        "substrate_contrast",
        "z_max_from_k",
        "detectability_margin",
        "detectable_mask",
        "band_detectability",
        "scene_detectability",
        "downwelling_kd",
        "interpolate_kd",
        "par_weights",
        "seabed_par_fraction",
        "euphotic_depth",
    ):
        from oceanstream.coastal import detectability as _detectability
        return getattr(_detectability, name)
    # High-level processor
    if name in ("CoastalProcessor", "CoastalResult"):
        from oceanstream.coastal.processor import CoastalProcessor, CoastalResult
        return {"CoastalProcessor": CoastalProcessor,
                "CoastalResult": CoastalResult}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
