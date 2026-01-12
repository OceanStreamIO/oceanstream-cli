"""Absorption coefficient calculation for acoustic processing.

Implements the Francois-Garrison (1982) equation for computing
frequency-dependent sound absorption in seawater.
"""

from __future__ import annotations

import logging
from typing import Union

import numpy as np

logger = logging.getLogger(__name__)

ArrayLike = Union[np.ndarray, float, list]


def compute_absorption_coefficient(
    frequency_hz: float,
    temperature: ArrayLike,
    salinity: ArrayLike,
    depth: ArrayLike,
    pH: float = 8.0,
) -> np.ndarray:
    """
    Compute absorption coefficient using Francois-Garrison (1982) equation.
    
    This is the standard equation for frequency-dependent sound absorption
    in seawater, accounting for boric acid, MgSO4, and pure water contributions.
    
    Args:
        frequency_hz: Frequency in Hz (e.g., 38000, 200000)
        temperature: Water temperature (°C)
        salinity: Salinity (PSU)
        depth: Depth (m)
        pH: Acidity (default 8.0 for seawater)
        
    Returns:
        Absorption coefficient (dB/m)
        
    Example:
        >>> compute_absorption_coefficient(38000, 15.0, 35.0, 100.0)
        0.0098...  # ~10 dB/km at 38kHz
        
    Reference:
        Francois, R.E., & Garrison, G.R. (1982). Sound absorption based on 
        ocean measurements. Part II: Boric acid contribution and equation 
        for total absorption. J. Acoust. Soc. Am., 72(6), 1879-1890.
    """
    return francois_garrison_absorption(
        frequency_hz, temperature, salinity, depth, pH
    )


def francois_garrison_absorption(
    frequency_hz: float = None,
    temperature: ArrayLike = None,
    salinity: ArrayLike = None,
    depth: ArrayLike = None,
    pH: float = 8.0,
    ph: float = None,
    frequency: float = None,
    sound_speed: float = None,
) -> np.ndarray:
    """
    Francois-Garrison (1982) absorption coefficient equation.
    
    Computes total absorption as sum of three contributions:
    1. Boric acid relaxation (low frequency)
    2. Magnesium sulfate (MgSO4) relaxation (mid frequency)
    3. Pure water viscous absorption (high frequency)
    
    Args:
        frequency_hz: Frequency in Hz
        frequency: Alias for frequency_hz
        temperature: Water temperature (°C)
        salinity: Salinity (PSU)
        depth: Depth (m) - used as pressure in dbar (approximately equal)
        pH: Acidity (default 8.0)
        ph: Alias for pH
        sound_speed: Sound speed (m/s). If None, computed from T, S, P.
        
    Returns:
        Absorption coefficient (dB/m)
        
    Reference:
        Francois, R.E., & Garrison, G.R. (1982). Sound absorption based on 
        ocean measurements. Part II: Boric acid contribution and equation 
        for total absorption. J. Acoust. Soc. Am., 72(6), 1879-1890.
    """
    # Handle aliases
    if frequency is not None:
        frequency_hz = frequency
    if ph is not None:
        pH = ph
    temperature = np.asarray(temperature)
    salinity = np.asarray(salinity)
    depth = np.asarray(depth)
    
    f_kHz = frequency_hz / 1000.0  # Convert to kHz
    T = temperature
    S = salinity
    # Use depth as pressure (in dbar, approximately equal to depth in meters)
    P = depth
    
    # Sound speed (m/s) - needed for A1 and A2 coefficients
    if sound_speed is None:
        c = 1412.0 + 3.21 * T + 1.19 * S + 0.0167 * P
    else:
        c = sound_speed
    
    # Convert temperature to Kelvin for relaxation frequencies
    T_kelvin = T + 273.0
    
    # ========================
    # 1. Boric acid contribution
    # ========================
    # Relaxation frequency (kHz)
    f1 = 2.8 * np.sqrt(S / 35.0) * 10**(4.0 - 1245.0 / T_kelvin)
    
    # Boric acid coefficient (note: uses sound speed c in denominator)
    A1 = 8.86 / c * 10**(0.78 * pH - 5)
    
    # Pressure correction
    P1 = 1.0
    
    # Boric acid absorption (dB/km)
    alpha1 = A1 * P1 * f1 * f_kHz**2 / (f_kHz**2 + f1**2)
    
    # ========================
    # 2. MgSO4 contribution
    # ========================
    # Relaxation frequency (kHz)
    f2 = (8.17 * 10**(8.0 - 1990.0 / T_kelvin)) / (1.0 + 0.0018 * (S - 35.0))
    
    # MgSO4 coefficient (note: uses sound speed c in denominator)
    A2 = 21.44 * S / c * (1.0 + 0.025 * T)
    
    # Pressure correction
    P2 = 1.0 - 1.37e-4 * P + 6.2e-9 * P**2
    
    # MgSO4 absorption (dB/km)
    alpha2 = A2 * P2 * f2 * f_kHz**2 / (f_kHz**2 + f2**2)
    
    # ========================
    # 3. Pure water contribution
    # ========================
    # Temperature-dependent viscosity coefficient
    # Use different formula for T >= 20°C
    if np.all(T < 20):
        A3 = 4.937e-4 - 2.59e-5 * T + 9.11e-7 * T**2 - 1.50e-8 * T**3
    else:
        A3 = 3.964e-4 - 1.146e-5 * T + 1.45e-7 * T**2 - 6.5e-10 * T**3
    
    # Pressure correction
    P3 = 1.0 - 3.83e-5 * P + 4.9e-10 * P**2
    
    # Pure water absorption (dB/km)
    alpha3 = A3 * P3 * f_kHz**2
    
    # ========================
    # Total absorption
    # ========================
    # Sum contributions (dB/km) and convert to dB/m
    alpha_total_db_km = alpha1 + alpha2 + alpha3
    
    return alpha_total_db_km / 1000.0


def absorption_simple(
    frequency_hz: float,
    temperature: float = 10.0,
) -> float:
    """
    Simple absorption approximation for quick estimates.
    
    Uses a simplified formula that depends mainly on frequency,
    assuming typical oceanographic conditions (T≈10°C, S≈35 PSU).
    
    Args:
        frequency_hz: Frequency in Hz
        temperature: Temperature (°C), default 10°C
        
    Returns:
        Absorption coefficient (dB/m)
    """
    f_kHz = frequency_hz / 1000.0
    
    # Simplified Ainslie-McColm approximation
    # Valid for typical ocean conditions
    alpha = (0.106 * f_kHz**2 / (1 + f_kHz**2) * np.exp((temperature - 10) / 26) +
             0.52 * (1 + temperature / 43) * f_kHz**2 / (4100 + f_kHz**2) * np.exp(-400 / (temperature + 273)) +
             0.00049 * f_kHz**2 * np.exp(-temperature / 27 + 400 / (temperature + 273)))
    
    # dB/km to dB/m
    return alpha / 1000.0


def get_typical_absorption(frequency_hz: float) -> dict:
    """
    Get typical absorption values for a given frequency.
    
    Returns approximate absorption for typical ocean conditions
    at different depths.
    
    Args:
        frequency_hz: Frequency in Hz
        
    Returns:
        Dict with absorption values at different depths
    """
    typical_params = {
        "temperature": 10.0,  # °C
        "salinity": 35.0,     # PSU
        "pH": 8.0,
    }
    
    depths = [0, 100, 500, 1000]
    
    return {
        "frequency_hz": frequency_hz,
        "conditions": typical_params,
        "absorption_by_depth": {
            d: float(francois_garrison_absorption(
                frequency_hz,
                typical_params["temperature"],
                typical_params["salinity"],
                d,
                typical_params["pH"],
            ))
            for d in depths
        },
    }
