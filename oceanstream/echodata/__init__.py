"""
OceanStream Echodata Processing Library.

This module provides both CLI commands and a Python library API for processing
echosounder data (EK60/EK80) into cloud-optimized formats with STAC metadata.

Usage as Library (for Prefect flows):
    from oceanstream.echodata import (
        convert_raw_files,
        apply_calibration,
        enrich_environment,
        concatenate_daily,
        compute_sv,
        apply_denoising,
        compute_mvbs,
        compute_nasc,
        generate_echograms,
        emit_stac,
    )

Usage as CLI:
    oceanstream process echodata convert --input-source ./raw --output-dir ./out

Dependencies:
    This module requires the echodata extras to be installed:
        pip install "oceanstream[echodata]"
    
    echopype is included automatically from the OceanStreamIO fork.
"""

from __future__ import annotations

# Lazy imports to avoid loading heavy dependencies unless needed
__all__ = [
    # Core processing
    "convert_raw_files",
    "convert_raw_file",
    "concatenate_daily",
    "group_files_by_day",
    "merge_location_data",
    "apply_calibration",
    "load_calibration",
    # Calibration
    "calibrate_saildrone",
    "load_saildrone_calibration",
    "detect_pulse_mode",
    # Environment
    "enrich_environment",
    "enrich_sv_with_location",
    "enrich_sv_with_location_from_url",
    "get_environment_with_fallback",
    "compute_sound_speed",
    "compute_absorption_coefficient",
    "fetch_copernicus_environment",
    "smooth_gps_track",
    # Compute
    "compute_sv",
    "compute_sv_from_echodata",
    "enrich_sv_dataset",
    "swap_range_to_depth",
    "correct_echo_range",
    "apply_corrections_ds",
    "compute_mvbs",
    "compute_nasc",
    # Denoise
    "apply_denoising",
    "build_noise_mask",
    "build_full_mask",
    "apply_noise_mask",
    "create_multichannel_mask",
    # Plot
    "generate_echograms",
    "plot_echogram",
    "plot_sv_data",
    "plot_sv_channel",
    "prepare_channel_da",
    "ensure_channel_labels",
    "plot_mask_channel",
    "plot_all_masks",
    "plot_masks_vertical",
    "create_interactive_echogram",
    "plot_sv_with_seabed",
    # Location / GPS
    "merge_location_data_from_gps",
    "interpolate_location_from_dataframe",
    "extract_location_data",
    "ramer_douglas_peucker",
    "extract_start_end_lat_lon",
    # STAC
    "emit_stac",
    "emit_echodata_collection",
    "emit_echodata_item",
    "add_echodata_to_collection",
    "get_echodata_summaries",
    "create_segments_geojson",
    "create_echogram_segment",
    "extract_segment_coordinates",
    # High-level processor
    "EchodataProcessor",
    "ProcessingResult",
    "PipelineResult",
    # Config
    "EchodataConfig",
    "DenoiseConfig",
    "MVBSConfig",
    "NASCConfig",
    # Pydantic denoise parameter models
    "MaskImpulseNoise",
    "TransientNoiseMask",
    "RemoveBackgroundNoise",
    "MaskAttenuatedSignal",
    "MVBSComputeOptions",
    "NASCComputeOptions",
    "fill_missing_frequency_params",
    # Consolidate (depth computation)
    "add_depth_to_sv",
    "choose_depth_flags",
    # Seabed detection
    "detect_seabed",
    "detect_seabed_maxSv",
    "detect_seabed_deltaSv",
    "detect_seabed_ariza",
    "detect_seabed_composite",
    "detect_seabed_blackwell",
    "mask_seabed",
    "compute_seabed_stats",
    "find_optimal_seabed_channel",
    "SeabedDetectionResult",
    "get_bathymetry",
    "estimate_seabed_depth",
    # Cloud Storage
    "get_azure_uri_and_options",
    "get_azure_zarr_store",
    "save_echodata_to_azure",
    "save_sv_to_azure",
    "save_product_to_azure",
    "open_sv_from_azure",
    "is_azure_configured",
    "list_campaign_data",
    # NetCDF export
    "save_to_netcdf",
    "save_to_netcdf_azure",
    "zip_netcdf_files",
    # NASC GeoParquet
    "export_nasc_to_geoparquet",
    "load_nasc_geoparquet",
    # Encoding utilities
    "fix_chunking",
    "get_variable_encoding",
    # Mask transform utilities
    "downsample",
    "upsample",
    "log",
    "lin",
    "line_to_square",
    "dask_nanmedian",
    "dask_nanmean",
]


def __getattr__(name: str):
    """Lazy import attributes to avoid loading heavy dependencies on import."""
    # Config classes
    if name in ("EchodataConfig", "DenoiseConfig", "MVBSConfig", "NASCConfig"):
        from oceanstream.echodata.config import (
            EchodataConfig,
            DenoiseConfig,
            MVBSConfig,
            NASCConfig,
        )
        return locals()[name]
    
    # Pydantic denoise parameter models
    if name in (
        "MaskImpulseNoise",
        "TransientNoiseMask",
        "RemoveBackgroundNoise",
        "MaskAttenuatedSignal",
        "MVBSComputeOptions",
        "NASCComputeOptions",
        "fill_missing_frequency_params",
    ):
        from oceanstream.echodata.models import (
            MaskImpulseNoise,
            TransientNoiseMask,
            RemoveBackgroundNoise,
            MaskAttenuatedSignal,
            MVBSComputeOptions,
            NASCComputeOptions,
            fill_missing_frequency_params,
        )
        return locals()[name]
    
    # Convert functions
    if name in ("convert_raw_files", "convert_raw_file"):
        from oceanstream.echodata.convert import convert_raw_files, convert_raw_file
        return locals()[name]
    
    # Concat functions
    if name in ("concatenate_daily", "group_files_by_day", "merge_location_data"):
        from oceanstream.echodata.concat import concatenate_daily, group_files_by_day, merge_location_data
        return locals()[name]
    
    # Calibration functions
    if name in ("apply_calibration", "load_calibration"):
        from oceanstream.echodata.calibrate import apply_calibration, load_calibration
        return locals()[name]
    
    # Saildrone calibration functions
    if name in ("calibrate_saildrone", "load_saildrone_calibration", "detect_pulse_mode"):
        from oceanstream.echodata.calibrate import (
            calibrate_saildrone,
            load_saildrone_calibration,
            detect_pulse_mode,
        )
        return locals()[name]
    
    # Environment functions
    if name in (
        "enrich_environment",
        "enrich_sv_with_location",
        "enrich_sv_with_location_from_url",
        "get_environment_with_fallback",
        "compute_sound_speed",
        "compute_absorption_coefficient",
        "fetch_copernicus_environment",
        "smooth_gps_track",
    ):
        from oceanstream.echodata.environment import (
            enrich_environment,
            enrich_sv_with_location,
            enrich_sv_with_location_from_url,
            get_environment_with_fallback,
            compute_sound_speed,
            compute_absorption_coefficient,
            fetch_copernicus_environment,
            smooth_gps_track,
        )
        return locals()[name]
    
    # Compute functions
    if name in ("compute_sv", "compute_sv_from_echodata", "enrich_sv_dataset", "swap_range_to_depth", "correct_echo_range", "apply_corrections_ds"):
        from oceanstream.echodata.compute.sv import (
            compute_sv,
            compute_sv_from_echodata,
            enrich_sv_dataset,
            swap_range_to_depth,
            correct_echo_range,
            apply_corrections_ds,
        )
        return locals()[name]
    
    if name in ("compute_mvbs", "compute_nasc"):
        from oceanstream.echodata.compute import compute_mvbs, compute_nasc
        return locals()[name]
    
    # Consolidate functions (depth computation)
    if name in ("add_depth_to_sv", "choose_depth_flags"):
        from oceanstream.echodata.consolidate import add_depth_to_sv, choose_depth_flags
        return locals()[name]
    
    # Seabed detection functions
    if name in (
        "detect_seabed",
        "detect_seabed_maxSv",
        "detect_seabed_deltaSv",
        "detect_seabed_ariza",
        "detect_seabed_composite",
        "detect_seabed_blackwell",
        "mask_seabed",
        "compute_seabed_stats",
        "find_optimal_seabed_channel",
        "SeabedDetectionResult",
    ):
        from oceanstream.echodata.seabed import (
            detect_seabed,
            detect_seabed_maxSv,
            detect_seabed_deltaSv,
            detect_seabed_ariza,
            detect_seabed_composite,
            detect_seabed_blackwell,
            mask_seabed,
            compute_seabed_stats,
            find_optimal_seabed_channel,
            SeabedDetectionResult,
        )
        return locals()[name]
    
    # Bathymetry functions
    if name in ("get_bathymetry", "estimate_seabed_depth"):
        from oceanstream.echodata.seabed.bathymetry import (
            get_bathymetry,
            estimate_seabed_depth,
        )
        return locals()[name]
    
    # Denoise functions
    if name in ("apply_denoising", "build_noise_mask", "build_full_mask", "apply_noise_mask", "create_multichannel_mask"):
        from oceanstream.echodata.denoise import (
            apply_denoising,
            build_noise_mask,
            build_full_mask,
            apply_noise_mask,
            create_multichannel_mask,
        )
        return locals()[name]
    
    # Plot functions
    if name in ("generate_echograms", "plot_echogram", "plot_sv_data", "plot_sv_channel", "prepare_channel_da", "ensure_channel_labels", "plot_mask_channel", "plot_all_masks", "plot_masks_vertical", "create_interactive_echogram", "plot_sv_with_seabed"):
        from oceanstream.echodata.plot import (
            generate_echograms,
            plot_echogram,
            plot_sv_data,
            plot_sv_channel,
            prepare_channel_da,
            ensure_channel_labels,
            plot_mask_channel,
            plot_all_masks,
            plot_masks_vertical,
            create_interactive_echogram,
            plot_sv_with_seabed,
        )
        return locals()[name]

    # Location / GPS functions
    if name in (
        "merge_location_data_from_gps",
        "interpolate_location_from_dataframe",
        "extract_location_data",
        "ramer_douglas_peucker",
        "extract_start_end_lat_lon",
    ):
        from oceanstream.echodata.consolidate.location import (
            merge_location_data as merge_location_data_from_gps,
            interpolate_location_from_dataframe,
            extract_location_data,
            ramer_douglas_peucker,
            extract_start_end_lat_lon,
        )
        return locals()[name]
    
    # STAC functions
    if name in (
        "emit_stac",
        "emit_echodata_collection",
        "emit_echodata_item",
        "add_echodata_to_collection",
        "get_echodata_summaries",
        "create_segments_geojson",
        "create_echogram_segment",
        "extract_segment_coordinates",
    ):
        from oceanstream.echodata.stac import (
            emit_stac,
            emit_echodata_collection,
            emit_echodata_item,
            add_echodata_to_collection,
            get_echodata_summaries,
            create_segments_geojson,
            create_echogram_segment,
            extract_segment_coordinates,
        )
        return locals()[name]
    
    # High-level processor
    if name == "EchodataProcessor":
        from oceanstream.echodata.processor import EchodataProcessor
        return EchodataProcessor
    
    if name in ("ProcessingResult", "PipelineResult"):
        from oceanstream.echodata.processor import ProcessingResult, PipelineResult
        return locals()[name]
    
    # Cloud storage functions
    if name in (
        "get_azure_uri_and_options",
        "get_azure_zarr_store",
        "save_echodata_to_azure",
        "save_sv_to_azure",
        "save_product_to_azure",
        "open_sv_from_azure",
        "is_azure_configured",
        "list_campaign_data",
    ):
        from oceanstream.echodata.storage import (
            get_azure_uri_and_options,
            get_azure_zarr_store,
            save_echodata_to_azure,
            save_sv_to_azure,
            save_product_to_azure,
            open_sv_from_azure,
            is_azure_configured,
            list_campaign_data,
        )
        return locals()[name]
    
    # NetCDF export functions
    if name in ("save_to_netcdf", "save_to_netcdf_azure", "zip_netcdf_files"):
        from oceanstream.echodata.export import (
            save_to_netcdf,
            save_to_netcdf_azure,
            zip_netcdf_files,
        )
        return locals()[name]
    
    # NASC GeoParquet export
    if name in ("export_nasc_to_geoparquet", "load_nasc_geoparquet"):
        from oceanstream.echodata.compute.nasc_export import (
            export_nasc_to_geoparquet,
            load_nasc_geoparquet,
        )
        return locals()[name]
    
    # Encoding utilities
    if name in ("fix_chunking", "get_variable_encoding"):
        from oceanstream.echodata.utils.encoding import (
            fix_chunking,
            get_variable_encoding,
        )
        return locals()[name]
    
    # Mask transform utilities
    if name in ("downsample", "upsample", "log", "lin", "line_to_square", "dask_nanmedian", "dask_nanmean"):
        from oceanstream.echodata.utils.mask_transforms import (
            downsample,
            upsample,
            log,
            lin,
            line_to_square,
            dask_nanmedian,
            dask_nanmean,
        )
        return locals()[name]
    
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
