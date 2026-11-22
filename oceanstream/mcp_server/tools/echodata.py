"""Echodata processing tools — echosounder data pipeline (EK60/EK80/AZFP)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..server import mcp_app

mcp = mcp_app

_ECHODATA_MISSING_MSG = (
    "Echodata dependencies not installed. "
    "Install with: pip install oceanstream[echodata]"
)


def _check_echodata_available() -> Optional[str]:
    """Return error message if echodata deps are not available, else None."""
    try:
        import echopype  # noqa: F401
        return None
    except ImportError:
        return _ECHODATA_MISSING_MSG


@mcp.tool()
def echodata_convert(
    input_source: str,
    output_dir: str = "./output",
    sonar_model: Optional[str] = None,
) -> dict[str, Any]:
    """Convert raw echosounder files (EK60/EK80/AZFP/AD2CP) to EchoData Zarr format.

    This is the first step in the echodata pipeline. Raw .raw files are parsed
    and converted to the standardized SONAR-netCDF4 format stored as Zarr.

    Args:
        input_source: Path to raw file(s) or directory containing raw files
        output_dir: Output directory for Zarr stores (default: ./output/)
        sonar_model: Sonar model hint (EK60, EK80, AZFP, AD2CP). Auto-detected from file if not specified.
    """
    err = _check_echodata_available()
    if err:
        return {"error": err}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        from oceanstream.echodata.processor import EchodataProcessor

        processor = EchodataProcessor(
            input_source=input_path,
            output_dir=Path(output_dir),
            sonar_model=sonar_model,
        )
        result = processor.convert()
        return {"status": "success", "result": _format_result(result)}
    except Exception as e:
        return {"error": f"Echodata convert failed: {e}"}


@mcp.tool()
def echodata_compute_sv(
    input_source: str,
    output_dir: str = "./output",
) -> dict[str, Any]:
    """Compute volume backscattering strength (Sv) from converted EchoData.

    Takes converted EchoData Zarr as input and produces an Sv dataset with
    calibrated acoustic backscatter values.

    Args:
        input_source: Path to converted EchoData Zarr store
        output_dir: Output directory for Sv dataset
    """
    err = _check_echodata_available()
    if err:
        return {"error": err}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        from oceanstream.echodata.processor import EchodataProcessor

        processor = EchodataProcessor(
            input_source=input_path,
            output_dir=Path(output_dir),
        )
        result = processor.compute_sv()
        return {"status": "success", "result": _format_result(result)}
    except Exception as e:
        return {"error": f"Sv computation failed: {e}"}


@mcp.tool()
def echodata_denoise(
    input_source: str,
    output_dir: str = "./output",
    config: Optional[str] = None,
) -> dict[str, Any]:
    """Apply denoising cascade to an Sv dataset.

    Applies a multi-step denoising pipeline: impulse noise → attenuation →
    transient noise → background noise → frequency differencing.

    Args:
        input_source: Path to Sv Zarr dataset
        output_dir: Output directory for denoised dataset
        config: Path to denoise configuration TOML file (optional, uses defaults if not specified)
    """
    err = _check_echodata_available()
    if err:
        return {"error": err}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        from oceanstream.echodata.processor import EchodataProcessor

        kwargs: dict[str, Any] = {}
        if config:
            kwargs["denoise_config"] = Path(config)

        processor = EchodataProcessor(
            input_source=input_path,
            output_dir=Path(output_dir),
            **kwargs,
        )
        result = processor.denoise()
        return {"status": "success", "result": _format_result(result)}
    except Exception as e:
        return {"error": f"Denoising failed: {e}"}


@mcp.tool()
def echodata_compute_mvbs(
    input_source: str,
    output_dir: str = "./output",
    range_bin: float = 5.0,
    ping_bin: str = "20s",
) -> dict[str, Any]:
    """Compute Mean Volume Backscattering Strength (MVBS).

    Reduces the Sv dataset by averaging over range and ping-time bins.

    Args:
        input_source: Path to (denoised) Sv dataset
        output_dir: Output directory for MVBS dataset
        range_bin: Range bin size in meters (default: 5.0)
        ping_bin: Ping time bin size as duration string (default: '20s')
    """
    err = _check_echodata_available()
    if err:
        return {"error": err}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        from oceanstream.echodata.processor import EchodataProcessor

        processor = EchodataProcessor(
            input_source=input_path,
            output_dir=Path(output_dir),
            range_bin=range_bin,
            ping_bin=ping_bin,
        )
        result = processor.compute_mvbs()
        return {"status": "success", "result": _format_result(result)}
    except Exception as e:
        return {"error": f"MVBS computation failed: {e}"}


@mcp.tool()
def echodata_compute_nasc(
    input_source: str,
    output_dir: str = "./output",
) -> dict[str, Any]:
    """Compute Nautical Area Scattering Coefficient (NASC).

    Integrates backscatter over depth to produce area-based scattering values
    used in fisheries acoustics abundance estimation.

    Args:
        input_source: Path to Sv dataset
        output_dir: Output directory for NASC dataset
    """
    err = _check_echodata_available()
    if err:
        return {"error": err}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        from oceanstream.echodata.processor import EchodataProcessor

        processor = EchodataProcessor(
            input_source=input_path,
            output_dir=Path(output_dir),
        )
        result = processor.compute_nasc()
        return {"status": "success", "result": _format_result(result)}
    except Exception as e:
        return {"error": f"NASC computation failed: {e}"}


@mcp.tool()
def echodata_enrich_location(
    input_source: str,
    output_dir: str = "./output",
    gps_file: Optional[str] = None,
) -> dict[str, Any]:
    """Add GPS/location data to echodata.

    Enriches an EchoData dataset with geographic coordinates from a GPS file
    or embedded Platform data.

    Args:
        input_source: Path to EchoData Zarr store
        output_dir: Output directory
        gps_file: Path to GPS data file (NMEA or CSV). Uses embedded Platform data if not specified.
    """
    err = _check_echodata_available()
    if err:
        return {"error": err}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        from oceanstream.echodata.processor import EchodataProcessor

        kwargs: dict[str, Any] = {}
        if gps_file:
            kwargs["gps_file"] = Path(gps_file)

        processor = EchodataProcessor(
            input_source=input_path,
            output_dir=Path(output_dir),
            **kwargs,
        )
        result = processor.enrich_location()
        return {"status": "success", "result": _format_result(result)}
    except Exception as e:
        return {"error": f"Location enrichment failed: {e}"}


@mcp.tool()
def echodata_seabed_mask(
    input_source: str,
    output_dir: str = "./output",
) -> dict[str, Any]:
    """Apply seabed detection and masking to an Sv dataset.

    Detects the seabed in the echogram and masks data below it.

    Args:
        input_source: Path to Sv dataset
        output_dir: Output directory for masked dataset
    """
    err = _check_echodata_available()
    if err:
        return {"error": err}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        from oceanstream.echodata.processor import EchodataProcessor

        processor = EchodataProcessor(
            input_source=input_path,
            output_dir=Path(output_dir),
        )
        result = processor.seabed_mask()
        return {"status": "success", "result": _format_result(result)}
    except Exception as e:
        return {"error": f"Seabed masking failed: {e}"}


@mcp.tool()
def echodata_plot(
    input_source: str,
    output_dir: str = "./output",
) -> dict[str, Any]:
    """Generate echogram plots from Sv or MVBS data.

    Produces PNG echogram images for visualization of acoustic data.

    Args:
        input_source: Path to Sv or MVBS dataset
        output_dir: Output directory for PNG images
    """
    err = _check_echodata_available()
    if err:
        return {"error": err}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        from oceanstream.echodata.processor import EchodataProcessor

        processor = EchodataProcessor(
            input_source=input_path,
            output_dir=Path(output_dir),
        )
        result = processor.plot()
        return {"status": "success", "result": _format_result(result)}
    except Exception as e:
        return {"error": f"Plot generation failed: {e}"}


@mcp.tool()
def echodata_run_pipeline(
    input_source: str,
    output_dir: str = "./output",
    sonar_model: Optional[str] = None,
    skip_denoise: bool = False,
    skip_mvbs: bool = False,
    skip_nasc: bool = False,
    skip_plot: bool = False,
) -> dict[str, Any]:
    """Run the full multi-step echodata pipeline.

    Executes the complete processing chain: convert → compute Sv → denoise →
    seabed mask → compute MVBS → compute NASC → generate echograms.

    Args:
        input_source: Path to raw echosounder file(s) or directory
        output_dir: Output directory for all pipeline outputs
        sonar_model: Sonar model (EK60, EK80, AZFP, AD2CP). Auto-detected if not specified.
        skip_denoise: Skip the denoising step
        skip_mvbs: Skip MVBS computation
        skip_nasc: Skip NASC computation
        skip_plot: Skip echogram plot generation
    """
    err = _check_echodata_available()
    if err:
        return {"error": err}

    input_path = Path(input_source)
    if not input_path.exists():
        return {"error": f"Input path not found: {input_source}"}

    try:
        from oceanstream.echodata.processor import EchodataProcessor

        processor = EchodataProcessor(
            input_source=input_path,
            output_dir=Path(output_dir),
            sonar_model=sonar_model,
        )
        result = processor.run(
            skip_denoise=skip_denoise,
            skip_mvbs=skip_mvbs,
            skip_nasc=skip_nasc,
            skip_plot=skip_plot,
        )
        return {"status": "success", "result": _format_result(result)}
    except Exception as e:
        return {"error": f"Pipeline failed: {e}"}


def _format_result(result: Any) -> Any:
    """Format a processing result for JSON serialization."""
    if result is None:
        return "completed"
    if hasattr(result, "__dict__"):
        return {k: str(v) for k, v in result.__dict__.items() if not k.startswith("_")}
    return str(result)
