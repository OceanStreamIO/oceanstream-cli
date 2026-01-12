"""Generic calibration interface for echosounder data.

Provides a unified interface for applying calibration to EchoData objects,
dispatching to provider-specific implementations as needed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from echopype.echodata import EchoData

logger = logging.getLogger(__name__)


def load_calibration(
    calibration_file: Path,
    provider: str = "auto",
) -> dict[str, Any]:
    """
    Load calibration values from a calibration file.
    
    Args:
        calibration_file: Path to calibration file (.xlsx, .ecs, .json)
        provider: Provider name for specific parsing ("saildrone", "auto")
        
    Returns:
        Dictionary of calibration values by frequency/pulse mode
        
    Raises:
        FileNotFoundError: If calibration_file doesn't exist
        ValueError: If file format is unsupported
    """
    calibration_file = Path(calibration_file)
    
    if not calibration_file.exists():
        raise FileNotFoundError(f"Calibration file not found: {calibration_file}")
    
    suffix = calibration_file.suffix.lower()
    
    if suffix == ".xlsx":
        # Excel format - likely Saildrone
        from oceanstream.echodata.calibrate.saildrone import load_saildrone_calibration
        return load_saildrone_calibration(calibration_file)
    
    elif suffix == ".ecs":
        # ECS format (Simrad calibration)
        return _load_ecs_calibration(calibration_file)
    
    elif suffix == ".json":
        # JSON format
        import json
        with open(calibration_file) as f:
            return json.load(f)
    
    else:
        raise ValueError(
            f"Unsupported calibration file format: {suffix}. "
            "Supported formats: .xlsx, .ecs, .json"
        )


def apply_calibration(
    echodata: "EchoData",
    calibration: Path | dict[str, Any],
    provider: str = "auto",
) -> "EchoData":
    """
    Apply calibration values to an EchoData object.
    
    Args:
        echodata: EchoData object to calibrate
        calibration: Path to calibration file or dict of calibration values
        provider: Provider name for specific handling ("saildrone", "auto")
        
    Returns:
        Calibrated EchoData object (modified in place)
        
    Example:
        ed = open_converted(zarr_path)
        ed = apply_calibration(ed, Path("calibration.xlsx"))
    """
    if isinstance(calibration, (str, Path)):
        calibration = load_calibration(Path(calibration), provider)
    
    # Detect provider from calibration structure if auto
    if provider == "auto":
        provider = _detect_provider(calibration)
    
    logger.info(f"Applying {provider} calibration")
    
    if provider == "saildrone":
        from oceanstream.echodata.calibrate.saildrone import calibrate_saildrone
        return calibrate_saildrone(echodata, calibration)
    else:
        # Generic calibration application
        return _apply_generic_calibration(echodata, calibration)


def _detect_provider(calibration: dict) -> str:
    """Detect provider from calibration dictionary structure."""
    if "38k short pulse" in calibration or "38k_short" in calibration:
        return "saildrone"
    return "generic"


def _apply_generic_calibration(
    echodata: "EchoData",
    calibration: dict[str, Any],
) -> "EchoData":
    """Apply generic calibration values to EchoData."""
    import numpy as np
    
    # Expected structure: {frequency: {parameter: value}}
    # Parameters: gain, sa_correction, beamwidth_alongship, etc.
    
    beam = echodata.beam if hasattr(echodata, 'beam') else echodata["Sonar/Beam_group1"]
    vendor = echodata["Vendor_specific"]
    
    freqs = beam.frequency_nominal.values.tolist()
    n_channels = len(freqs)
    
    for i, freq in enumerate(freqs):
        freq_key = f"{int(freq/1000)}kHz"
        if freq_key not in calibration:
            logger.warning(f"No calibration for {freq_key}, skipping")
            continue
        
        cal = calibration[freq_key]
        
        if "gain" in cal:
            vendor.gain_correction.values[i, :] = cal["gain"]
        
        if "sa_correction" in cal:
            vendor.sa_correction.values[i, :] = cal["sa_correction"]
        
        if "beamwidth_alongship" in cal:
            beam.beamwidth_twoway_alongship.values[i] = cal["beamwidth_alongship"]
        
        if "beamwidth_athwartship" in cal:
            beam.beamwidth_twoway_athwartship.values[i] = cal["beamwidth_athwartship"]
        
        if "angle_offset_alongship" in cal:
            beam.angle_offset_alongship.values[i] = cal["angle_offset_alongship"]
        
        if "angle_offset_athwartship" in cal:
            beam.angle_offset_athwartship.values[i] = cal["angle_offset_athwartship"]
    
    logger.info(f"Applied calibration to {n_channels} channels")
    return echodata


def _load_ecs_calibration(ecs_file: Path) -> dict[str, Any]:
    """Load calibration from Simrad ECS file format."""
    # ECS files are INI-style with [ChannelN] sections
    # This is a simplified parser
    import configparser
    
    config = configparser.ConfigParser()
    config.read(ecs_file)
    
    calibration = {}
    
    for section in config.sections():
        if section.startswith("Channel"):
            freq = config.getfloat(section, "Frequency", fallback=0)
            freq_key = f"{int(freq/1000)}kHz"
            
            calibration[freq_key] = {
                "gain": config.getfloat(section, "Gain", fallback=0),
                "sa_correction": config.getfloat(section, "SaCorrection", fallback=0),
                "beamwidth_alongship": config.getfloat(section, "BeamWidthAlongship", fallback=0),
                "beamwidth_athwartship": config.getfloat(section, "BeamWidthAthwartship", fallback=0),
                "angle_offset_alongship": config.getfloat(section, "AngleOffsetAlongship", fallback=0),
                "angle_offset_athwartship": config.getfloat(section, "AngleOffsetAthwartship", fallback=0),
            }
    
    return calibration


def validate_calibration_params(params: dict) -> bool:
    """
    Validate calibration parameters dictionary.
    
    Args:
        params: Dictionary of calibration parameters by frequency
        
    Returns:
        True if valid
        
    Raises:
        ValueError: If invalid frequency type
        TypeError: If parameter types are wrong
    """
    for freq_key, values in params.items():
        # Frequency keys should be numeric (int)
        if not isinstance(freq_key, (int, float)):
            raise TypeError(f"Frequency key must be numeric, got {type(freq_key)}")
        
        # Values should be a dict
        if not isinstance(values, dict):
            raise ValueError(f"Calibration values for {freq_key} must be a dict")
    
    return True


def parse_ecs_file(ecs_file: Path) -> dict[int, dict]:
    """
    Parse Simrad ECS calibration file format.
    
    Args:
        ecs_file: Path to .ecs file
        
    Returns:
        Dictionary of calibration values keyed by frequency (Hz)
    """
    import xml.etree.ElementTree as ET
    
    tree = ET.parse(ecs_file)
    root = tree.getroot()
    
    params = {}
    
    for cal in root.findall(".//Calibration"):
        freq = int(cal.get("Frequency", 0))
        params[freq] = {}
        
        gain_elem = cal.find("Gain")
        if gain_elem is not None:
            params[freq]["gain"] = float(gain_elem.text)
        
        sa_elem = cal.find("SaCorrection")
        if sa_elem is not None:
            params[freq]["sa_correction"] = float(sa_elem.text)
    
    return params


def parse_json_calibration(json_file: Path) -> dict[int, dict]:
    """
    Parse JSON calibration file format.
    
    Args:
        json_file: Path to .json file
        
    Returns:
        Dictionary of calibration values keyed by frequency (Hz)
    """
    import json
    
    with open(json_file) as f:
        data = json.load(f)
    
    params = {}
    
    # Handle frequencies key or direct dict
    freq_data = data.get("frequencies", data)
    
    for freq_str, values in freq_data.items():
        freq = int(freq_str)
        params[freq] = values
    
    return params
