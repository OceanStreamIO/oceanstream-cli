"""Saildrone-specific calibration for EK80 echosounders.

Handles Excel calibration files with pulse mode detection (short/long)
for 38kHz and 200kHz frequencies used on Saildrone USVs.

Ported from _echodata-legacy-code/saildrone-echodata-processing/calibrate/saildrone.py
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from echopype.echodata import EchoData
    import pandas as pd

logger = logging.getLogger(__name__)

# Pulse duration thresholds
SHORT_MS = 1.024e-3  # 1.024 ms → "short"
LONG_MS = 2.048e-3   # 2.048 ms → "long"

# Regex for extracting first numeric token from Excel values
_NUM_PATTERN = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def _robust_float(x: Any) -> float:
    """
    Robustly convert value to float, handling various formats.
    
    Handles:
    - bytes → decode
    - str → normalize NBSP/fancy minus, extract first number
    - else → float(x) or nan
    """
    try:
        if isinstance(x, (bytes, bytearray)):
            x = x.decode("utf-8", "ignore")
        
        if isinstance(x, str):
            txt = (
                x.replace("\xa0", " ")      # NBSP → space
                .replace("\u2212", "-")     # math minus → hyphen
                .strip()
            )
            m = _NUM_PATTERN.search(txt)
            return float(m.group(0)) if m else np.nan
        
        return float(x)
    except Exception:
        return np.nan


def load_saildrone_calibration(file_path: Path) -> dict[str, Any]:
    """
    Load calibration values from Saildrone Excel file.
    
    Expected Excel format (Sheet1):
    Row 0: Header
    Row 1: Column labels (Variable, 38k short pulse, 38k long pulse, 200k short pulse)
    Row 2+: Calibration values
    
    Rows in order:
    0: pulse_length (ms)
    1: Gain (dB)
    2: beamwidth_alongship (°)
    3: beamwidth_athwartship (°)
    4: angle_offset_alongship (°)
    5: angle_offset_athwartship (°)
    6: Sa_corr (dB)
    
    Args:
        file_path: Path to Excel calibration file
        
    Returns:
        Dictionary with calibration values by frequency/pulse mode
    """
    try:
        import pandas as pd
    except ImportError as e:
        raise ImportError("pandas and openpyxl required: pip install pandas openpyxl") from e
    
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"Calibration file not found: {file_path}")
    
    cal = pd.read_excel(file_path, sheet_name="Sheet1", header=1, usecols="A:D")
    
    # Normalize column names
    cal.columns = ["Variable", "38k short pulse", "38k long pulse", "200k short pulse"]
    
    # Convert all values to float
    cal = cal.map(_robust_float)
    
    logger.info(f"Loaded calibration from {file_path}")
    
    return {
        "dataframe": cal,
        "38k_short": {
            "gain": cal.at[1, "38k short pulse"],
            "beamwidth_alongship": cal.at[2, "38k short pulse"],
            "beamwidth_athwartship": cal.at[3, "38k short pulse"],
            "angle_offset_alongship": cal.at[4, "38k short pulse"],
            "angle_offset_athwartship": cal.at[5, "38k short pulse"],
            "sa_correction": cal.at[6, "38k short pulse"],
        },
        "38k_long": {
            "gain": cal.at[1, "38k long pulse"],
            "beamwidth_alongship": cal.at[2, "38k long pulse"],
            "beamwidth_athwartship": cal.at[3, "38k long pulse"],
            "angle_offset_alongship": cal.at[4, "38k long pulse"],
            "angle_offset_athwartship": cal.at[5, "38k long pulse"],
            "sa_correction": cal.at[6, "38k long pulse"],
        },
        "200k_short": {
            "gain": cal.at[1, "200k short pulse"],
            "beamwidth_alongship": cal.at[2, "200k short pulse"],
            "beamwidth_athwartship": cal.at[3, "200k short pulse"],
            "angle_offset_alongship": cal.at[4, "200k short pulse"],
            "angle_offset_athwartship": cal.at[5, "200k short pulse"],
            "sa_correction": cal.at[6, "200k short pulse"],
        },
    }


def detect_pulse_mode(echodata: "EchoData", atol: float = 5e-6) -> list[str]:
    """
    Detect pulse mode (short/long) for each channel.
    
    Uses transmit_duration_nominal from Sonar/Beam_group1 to determine
    if each channel uses short (1.024ms) or long (2.048ms) pulse.
    
    Args:
        echodata: EchoData object
        atol: Absolute tolerance for pulse duration comparison
        
    Returns:
        List of "short" or "long" for each channel
        
    Raises:
        ValueError: If a channel has mixed pulse durations
    """
    td = echodata["Sonar/Beam_group1"].transmit_duration_nominal  # (ping_time, channel)
    
    # Get first ping values as baseline
    first = td.isel(ping_time=0).values.astype(float)
    td_vals = td.values.astype(float)
    
    # Check for consistency across pings
    for ch, col in enumerate(td_vals.T):
        if not np.allclose(col, first[ch], atol=atol):
            uniq = np.unique(np.round(col, 6))
            if len(uniq) == 1:
                first[ch] = uniq[0]
            else:
                raise ValueError(
                    f"Channel {ch} has multiple pulse durations {uniq}; "
                    "cannot determine short vs long automatically."
                )
    
    pulse_modes = []
    for d in first:
        if np.isclose(d, SHORT_MS, atol=atol):
            pulse_modes.append("short")
        elif np.isclose(d, LONG_MS, atol=atol) or d > 1.5e-3:
            pulse_modes.append("long")
        else:
            raise ValueError(f"Unknown pulse duration {d:.6f}s")
    
    return pulse_modes


def calibrate_saildrone(
    echodata: "EchoData",
    calibration: dict[str, Any],
) -> "EchoData":
    """
    Apply Saildrone calibration to EchoData object.
    
    Updates gain_correction, sa_correction, beamwidth, and angle_offset
    based on frequency and detected pulse mode.
    
    Args:
        echodata: EchoData object to calibrate
        calibration: Calibration dict from load_saildrone_calibration
        
    Returns:
        Calibrated EchoData object (modified in place)
    """
    # Get calibration dataframe
    if "dataframe" in calibration:
        cal = calibration["dataframe"]
    else:
        raise ValueError("Invalid calibration format - missing 'dataframe' key")
    
    # Get vendor and beam groups
    vendor = echodata["Vendor_specific"]
    beam = echodata["Sonar/Beam_group1"]
    
    gain_var = vendor.gain_correction
    n_chans, n_pl_bins = gain_var.shape
    n_sa_bins = vendor.sa_correction.shape[1]
    
    # Get frequencies and pulse modes
    freqs = beam.frequency_nominal.values.tolist()
    modes = detect_pulse_mode(echodata)
    
    logger.info(f"Frequencies: {freqs}, Pulse modes: {modes}")
    
    # Map frequency/mode to calibration columns
    cols = []
    for f, mode in zip(freqs, modes):
        if np.isclose(f, 38_000.0):
            cols.append("38k short pulse" if mode == "short" else "38k long pulse")
        elif np.isclose(f, 200_000.0):
            cols.append("200k short pulse")  # Saildrone never uses long at 200 kHz
        else:
            raise ValueError(f"Unsupported frequency {f}")
    
    # Handle single channel files
    if len(cols) == 1 and n_chans > 1:
        cols = cols * n_chans
    
    def tile_row(row_idx: int, tgt_bins: int) -> np.ndarray:
        """Tile calibration row values across bins."""
        return np.vstack([
            np.full(tgt_bins, cal.at[row_idx, c]) for c in cols
        ])
    
    # Apply gain (row 1) - (n_chans × n_pl_bins)
    vendor.gain_correction.values = tile_row(1, n_pl_bins)
    
    # Get shapes for 1D arrays
    bw_shape = beam.beamwidth_twoway_alongship.shape[0]
    ao_shape = beam.angle_offset_alongship.shape[0]
    
    # Apply beamwidths (rows 2-3)
    beam.beamwidth_twoway_alongship.values = [
        cal.at[2, c] for c in cols[:bw_shape]
    ]
    beam.beamwidth_twoway_athwartship.values = [
        cal.at[3, c] for c in cols[:bw_shape]
    ]
    
    # Apply angle offsets (rows 4-5)
    beam.angle_offset_alongship.values = [
        cal.at[4, c] for c in cols[:ao_shape]
    ]
    beam.angle_offset_athwartship.values = [
        cal.at[5, c] for c in cols[:ao_shape]
    ]
    
    # Apply Sa correction (row 6) - (n_chans × n_sa_bins)
    vendor.sa_correction.values = tile_row(6, n_sa_bins)
    
    logger.info(f"Applied Saildrone calibration to {n_chans} channels")
    return echodata


# Alias for test compatibility
parse_calibration_excel = load_saildrone_calibration

# Required columns for calibration files
REQUIRED_COLUMNS = [
    "frequency",
    "gain",
    "sa_correction", 
    "beamwidth_alongship",
    "beamwidth_athwartship",
]
