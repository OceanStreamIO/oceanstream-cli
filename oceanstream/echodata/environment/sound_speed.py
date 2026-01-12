"""Sound speed calculation equations for acoustic processing.

Implements UNESCO Chen-Millero (1983) and Mackenzie (1981) equations
for computing sound speed in seawater from temperature, salinity, and depth.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Union

import numpy as np

if TYPE_CHECKING:
    import xarray as xr

logger = logging.getLogger(__name__)

# Type alias for array-like inputs
ArrayLike = Union[np.ndarray, float, list]


def compute_sound_speed(
    temperature: ArrayLike,
    salinity: ArrayLike,
    depth: ArrayLike,
    method: str = "chen_millero",
) -> np.ndarray:
    """
    Compute sound speed in seawater.
    
    Args:
        temperature: Water temperature (°C)
        salinity: Practical salinity (PSU)
        depth: Depth (m)
        method: Equation to use:
            - "chen_millero": UNESCO Chen-Millero (1983) - recommended for in-situ CTD
            - "mackenzie": Mackenzie (1981) - simpler, good for model data
            
    Returns:
        Sound speed (m/s)
        
    Example:
        >>> compute_sound_speed(15.0, 35.0, 100.0)
        1506.7...
    """
    temperature = np.asarray(temperature)
    salinity = np.asarray(salinity)
    depth = np.asarray(depth)
    
    if method == "chen_millero":
        return chen_millero_sound_speed(temperature, salinity, depth)
    elif method == "mackenzie":
        return mackenzie_sound_speed(temperature, salinity, depth)
    else:
        raise ValueError(f"Unknown sound speed method: {method}. Use 'chen_millero' or 'mackenzie'.")


def chen_millero_sound_speed(
    temperature: np.ndarray,
    salinity: np.ndarray,
    depth: np.ndarray = None,
    pressure: np.ndarray = None,
) -> np.ndarray:
    """
    Compute sound speed using Chen-Millero equation (UNESCO, 1983).
    
    This is the UNESCO standard equation for sound speed in seawater,
    accurate to ±0.1 m/s for typical oceanographic conditions.
    
    Valid ranges:
        Temperature: 0-40°C
        Salinity: 0-40 PSU
        Depth: 0-8000m
    
    Args:
        temperature: In-situ temperature (°C)
        salinity: Practical salinity (PSU)
        depth: Depth (m) - alternative to pressure
        pressure: Pressure (dbar) - alternative to depth
        
    Returns:
        Sound speed (m/s)
        
    Reference:
        Chen, C.T., & Millero, F.J. (1977). Speed of sound in seawater at 
        high pressures. J. Acoust. Soc. Am., 62(5), 1129-1135.
        
        UNESCO (1983). Algorithms for computation of fundamental properties 
        of seawater. Technical Papers in Marine Science, 44.
    """
    T = temperature
    S = salinity
    
    # Handle depth vs pressure input
    if pressure is not None:
        P = np.asarray(pressure)
    elif depth is not None:
        # Convert depth to pressure (dbar) - simplified
        # More accurate conversion would use latitude
        P = np.asarray(depth) / 10.0  # Approximate: 1 dbar ≈ 1m
    else:
        P = np.zeros_like(temperature)  # Surface
    
    # Pure water sound speed
    C0 = (1402.388
          + 5.03830 * T
          - 5.81090e-2 * T**2
          + 3.3432e-4 * T**3
          - 1.47797e-6 * T**4
          + 3.1419e-9 * T**5)
    
    # Salinity correction
    A = (1.389
         - 1.262e-2 * T
         + 7.166e-5 * T**2
         + 2.008e-6 * T**3
         - 3.21e-8 * T**4)
    
    B = (9.4742e-5
         - 1.2583e-5 * T
         - 6.4928e-8 * T**2
         + 1.0515e-8 * T**3
         - 2.0142e-10 * T**4)
    
    D = -1.329e-6 - 1.05e-9 * T - 1.041e-10 * T**2
    
    C_S = C0 + A * S + B * S**1.5 + D * S**2
    
    # Pressure correction
    E = (1.50073e-5
         + 3.927e-8 * T
         - 1.031e-9 * T**2
         + 1.207e-11 * T**3)
    
    F = (-1.389e-6
         + 9.484e-10 * T
         + 2.229e-10 * T**2)
    
    G = 1.4011e-7
    
    H = (1.0692e-8
         - 5.79e-11 * T
         + 2.73e-12 * T**2)
    
    I = (-2.9929e-11
         + 9.66e-14 * T)
    
    J = 4.5116e-14
    
    K = (-4.1349e-10
         - 1.5098e-11 * T
         + 1.8345e-13 * T**2)
    
    L = (4.2525e-12
         + 2.7965e-14 * T)
    
    M = -5.2264e-15
    
    C_P = (P * E + P**2 * F + P**3 * G
           + S * (P * H + P**2 * I + P**3 * J)
           + S**1.5 * (P * K + P**2 * L + P**3 * M))
    
    return C_S + C_P


def mackenzie_sound_speed(
    temperature: np.ndarray,
    salinity: np.ndarray,
    depth: np.ndarray,
) -> np.ndarray:
    """
    Compute sound speed using Mackenzie (1981) equation.
    
    This is a simpler 9-term equation that's commonly used for
    model-derived data (e.g., from Copernicus Marine Service).
    
    Valid ranges:
        Temperature: 2-30°C
        Salinity: 25-40 PSU
        Depth: 0-8000m
    
    Args:
        temperature: In-situ temperature (°C) - NOT potential temperature
        salinity: Practical salinity (PSU)
        depth: Depth (m)
        
    Returns:
        Sound speed (m/s)
        
    Reference:
        Mackenzie, K.V. (1981). Nine-term equation for sound speed in 
        the oceans. J. Acoust. Soc. Am., 70(3), 807-812.
    """
    T = temperature
    S = salinity
    D = depth
    
    return (1448.96
            + 4.591 * T
            - 0.05304 * T**2
            + 2.374e-4 * T**3
            + 1.340 * (S - 35)
            + 0.0163 * D
            + 1.675e-7 * D**2
            - 0.01025 * T * (S - 35)
            - 7.139e-13 * T * D**3)


def sound_speed_simple(
    temperature: ArrayLike,
    salinity: ArrayLike,
    depth: ArrayLike,
) -> np.ndarray:
    """
    Simple sound speed approximation for quick estimates.
    
    This is a highly simplified formula useful for quick estimates
    when accuracy is not critical.
    
    Args:
        temperature: Temperature (°C)
        salinity: Salinity (PSU)
        depth: Depth (m)
        
    Returns:
        Sound speed (m/s)
    """
    T = np.asarray(temperature)
    S = np.asarray(salinity)
    D = np.asarray(depth)
    
    # UNESCO simplified
    return (1449.2
            + 4.6 * T
            - 0.055 * T**2
            + 0.00029 * T**3
            + (1.34 - 0.010 * T) * (S - 35)
            + 0.016 * D)


def compute_sound_speed_from_copernicus(
    env_ds: "xr.Dataset",
    latitude: float,
    target_depth: float = 5.0,
) -> tuple[float, float]:
    """
    Compute sound speed from Copernicus environment dataset.
    
    Uses Mackenzie formula which is appropriate for model-derived data.
    
    Args:
        env_ds: Copernicus environment dataset (from fetch_copernicus_environment)
        latitude: Latitude for pressure calculation
        target_depth: Depth at which to extract values (m)
        
    Returns:
        Tuple of (sound_speed_m_s, temperature_C)
    """
    # Select nearest depth
    env_at_depth = env_ds.sel(depth=target_depth, method="nearest")
    
    # Get potential temperature and salinity
    potential_temp = float(env_at_depth["thetao"].values)
    salinity = float(env_at_depth["so"].values)
    depth = float(env_at_depth["depth"].values)
    
    # Convert potential to in-situ temperature
    try:
        import gsw
        pressure = gsw.p_from_z(-depth, latitude)
        CT = gsw.CT_from_pt(salinity, potential_temp)
        temp_insitu = float(gsw.t_from_CT(salinity, CT, pressure))
    except ImportError:
        # If gsw not available, use potential temp directly (small error at shallow depths)
        logger.warning("gsw not available, using potential temperature directly")
        temp_insitu = potential_temp
    
    # Compute sound speed using Mackenzie formula
    sound_speed = float(mackenzie_sound_speed(temp_insitu, salinity, depth))
    
    logger.info(
        f"Copernicus environment at {depth:.1f}m: "
        f"T={temp_insitu:.2f}°C, S={salinity:.2f} PSU, c={sound_speed:.1f} m/s"
    )
    
    return sound_speed, temp_insitu
