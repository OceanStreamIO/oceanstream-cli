"""Blended environment profiles combining in-situ surface with model depth data.

For Saildrone acoustic processing, this module provides functions to:
1. Use high-quality in-situ CTD data at the surface (SBE37 @ ~0.5m)
2. Use Copernicus Marine model data for the water column below

This is important for deep targets where sound speed varies significantly
through the thermocline.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional, Tuple

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    import xarray as xr

from oceanstream.echodata.environment.absorption import francois_garrison_absorption
from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed

logger = logging.getLogger(__name__)


def build_blended_profile(
    insitu_temp: float,
    insitu_sal: float,
    insitu_depth: float,
    copernicus_profile: dict,
    blend_depth: float = 5.0,
    pH: float = 8.1,
) -> dict:
    """
    Build a blended T/S/c/α profile from in-situ surface + Copernicus depth.
    
    Uses in-situ measurements at the surface (most accurate for near-transducer
    conditions) and model data for the water column below.
    
    Args:
        insitu_temp: In-situ temperature (°C) from Saildrone CTD
        insitu_sal: In-situ salinity (PSU) from Saildrone CTD
        insitu_depth: Depth of in-situ measurement (m), typically 0.5m for SBE37
        copernicus_profile: Dict from get_copernicus_profile() with keys:
            - depth: list of depths (m)
            - temperature: list of temperatures (°C)
            - salinity: list of salinities (PSU)
        blend_depth: Depth below which to use Copernicus data (default 5m)
        pH: Seawater pH for absorption calculation (default 8.1)
        
    Returns:
        Dict with blended profile:
            - depth: list of depths (m)
            - temperature: list of temperatures (°C)
            - salinity: list of salinities (PSU)
            - sound_speed: list of sound speeds (m/s)
            - source: list of data sources ('insitu' or 'copernicus')
    """
    # Start with in-situ surface
    depths = [insitu_depth]
    temps = [insitu_temp]
    sals = [insitu_sal]
    sources = ['insitu']
    
    # Add Copernicus depths below blend_depth
    for i, d in enumerate(copernicus_profile['depth']):
        if d >= blend_depth:
            depths.append(d)
            temps.append(copernicus_profile['temperature'][i])
            sals.append(copernicus_profile['salinity'][i])
            sources.append('copernicus')
    
    # Compute derived parameters
    sound_speeds = [
        mackenzie_sound_speed(t, s, d) 
        for t, s, d in zip(temps, sals, depths)
    ]
    
    return {
        'depth': depths,
        'temperature': temps,
        'salinity': sals,
        'sound_speed': sound_speeds,
        'source': sources,
    }


def compute_depth_weighted_env_params(
    blended_profile: dict,
    target_depth: float,
    frequency_hz: float,
    pH: float = 8.1,
) -> Tuple[float, float]:
    """
    Compute effective sound speed and absorption for a target at given depth.
    
    Uses harmonic mean of sound speed (appropriate for two-way travel time)
    and linear average of absorption (appropriate for cumulative attenuation).
    
    Args:
        blended_profile: Output from build_blended_profile()
        target_depth: Depth of acoustic target (m)
        frequency_hz: Frequency for absorption calculation (Hz)
        pH: Seawater pH (default 8.1)
        
    Returns:
        Tuple of (effective_sound_speed_m_s, total_absorption_dB_m)
    """
    depths = np.array(blended_profile['depth'])
    temps = np.array(blended_profile['temperature'])
    sals = np.array(blended_profile['salinity'])
    
    # Interpolate to fine depth grid for integration
    fine_depths = np.linspace(depths[0], min(target_depth, depths[-1]), 100)
    fine_temps = np.interp(fine_depths, depths, temps)
    fine_sals = np.interp(fine_depths, depths, sals)
    
    # Compute sound speed and absorption at each depth
    fine_c = np.array([
        mackenzie_sound_speed(t, s, d)
        for t, s, d in zip(fine_temps, fine_sals, fine_depths)
    ])
    fine_alpha = np.array([
        francois_garrison_absorption(frequency_hz, t, s, d, pH)
        for t, s, d in zip(fine_temps, fine_sals, fine_depths)
    ])
    
    # Harmonic mean of sound speed (weighted by layer thickness)
    # This is appropriate because travel time = distance / speed
    # Total time = sum(Δz / c_i), so effective c = total_distance / total_time
    dz = np.diff(fine_depths)
    segment_c = (fine_c[:-1] + fine_c[1:]) / 2  # Average c in each segment
    total_time = np.sum(dz / segment_c)
    total_distance = fine_depths[-1] - fine_depths[0]
    effective_c = total_distance / total_time if total_time > 0 else fine_c[0]
    
    # Linear average of absorption (cumulative attenuation)
    segment_alpha = (fine_alpha[:-1] + fine_alpha[1:]) / 2
    effective_alpha = np.mean(segment_alpha)
    
    return float(effective_c), float(effective_alpha)


def get_blended_env_params_for_calibration(
    insitu_df: "pd.DataFrame",
    copernicus_profile: dict,
    ping_times: "pd.DatetimeIndex",
    channels: list,
    target_depth: float = None,
    frequency_hz: float = None,
    frequencies_hz: list = None,
    blend_depth: float = 5.0,
    pH: float = 8.1,
    temp_col: str = None,
    sal_col: str = None,
) -> Tuple["xr.DataArray", "xr.DataArray"]:
    """
    Create env_params DataArrays for echopype compute_Sv using blended profiles.
    
    Args:
        insitu_df: DataFrame with columns: time, temperature, salinity
        copernicus_profile: Output from get_copernicus_profile()
        ping_times: Ping times from EchoData
        channels: Channel names from EchoData
        target_depth: Target depth for depth-weighted parameters (m)
            If None, uses surface values only
        frequency_hz: Single frequency (Hz) for absorption
        frequencies_hz: List of frequencies (Hz) if multiple channels
        blend_depth: Depth below which to use Copernicus (default 5m)
        pH: Seawater pH (default 8.1)
        temp_col: Name of temperature column (auto-detected if None)
        sal_col: Name of salinity column (auto-detected if None)
        
    Returns:
        Tuple of (sound_speed_da, absorption_da) ready for compute_Sv env_params
        
    Example:
        >>> ss_da, abs_da = get_blended_env_params_for_calibration(
        ...     insitu_df=saildrone_ctd,
        ...     copernicus_profile=copernicus,
        ...     ping_times=ping_times,
        ...     channels=channels,
        ...     target_depth=100.0,  # For targets at 100m
        ...     frequencies_hz=[38000, 200000],
        ... )
        >>> ds_Sv = ep.calibrate.compute_Sv(echodata, env_params={
        ...     'sound_speed': ss_da,
        ...     'sound_absorption': abs_da,
        ... })
    """
    import xarray as xr
    
    # Handle frequency specification
    if frequencies_hz is None and frequency_hz is not None:
        frequencies_hz = [frequency_hz] * len(channels)
    elif frequencies_hz is None:
        # Default frequencies for common Saildrone transducers
        frequencies_hz = [38000, 200000][:len(channels)]
    
    # Auto-detect column names
    if temp_col is None:
        temp_candidates = ['TEMP_SBE37_MEAN', 'temperature', 'temp', 'TEMP']
        for col in temp_candidates:
            if col in insitu_df.columns:
                temp_col = col
                break
        if temp_col is None:
            raise ValueError(f"Temperature column not found. Tried: {temp_candidates}")
    
    if sal_col is None:
        sal_candidates = ['SAL_SBE37_MEAN', 'salinity', 'sal', 'SAL']
        for col in sal_candidates:
            if col in insitu_df.columns:
                sal_col = col
                break
        if sal_col is None:
            raise ValueError(f"Salinity column not found. Tried: {sal_candidates}")
    
    # Get in-situ time series
    insitu_times = pd.to_datetime(insitu_df['time']).dt.tz_localize(None).values
    insitu_temps = pd.to_numeric(insitu_df[temp_col], errors='coerce').values
    insitu_sals = pd.to_numeric(insitu_df[sal_col], errors='coerce').values
    insitu_depth = 0.5  # SBE37 typical depth
    
    # Remove duplicates and NaN values (echopype requires unique time coords)
    time_series = pd.Series(insitu_times)
    mask = ~time_series.duplicated() & ~np.isnan(insitu_temps) & ~np.isnan(insitu_sals)
    insitu_times = insitu_times[mask]
    insitu_temps = insitu_temps[mask]
    insitu_sals = insitu_sals[mask]
    
    # Sort by time
    sort_idx = np.argsort(insitu_times)
    insitu_times = insitu_times[sort_idx]
    insitu_temps = insitu_temps[sort_idx]
    insitu_sals = insitu_sals[sort_idx]
    
    n_times = len(insitu_times)
    n_channels = len(channels)
    
    # Compute env params for each time point
    sound_speeds = np.zeros((n_times, n_channels))
    absorptions = np.zeros((n_times, n_channels))
    
    for i in range(n_times):
        # Build blended profile for this time
        profile = build_blended_profile(
            insitu_temp=insitu_temps[i],
            insitu_sal=insitu_sals[i],
            insitu_depth=insitu_depth,
            copernicus_profile=copernicus_profile,
            blend_depth=blend_depth,
            pH=pH,
        )
        
        if target_depth is not None and target_depth > blend_depth:
            # Use depth-weighted parameters for deep targets
            for j, freq in enumerate(frequencies_hz):
                c, alpha = compute_depth_weighted_env_params(
                    profile, target_depth, freq, pH
                )
                sound_speeds[i, j] = c
                absorptions[i, j] = alpha
        else:
            # Use surface values (simple case)
            for j, freq in enumerate(frequencies_hz):
                sound_speeds[i, j] = mackenzie_sound_speed(
                    insitu_temps[i], insitu_sals[i], insitu_depth
                )
                absorptions[i, j] = francois_garrison_absorption(
                    freq, insitu_temps[i], insitu_sals[i], insitu_depth, pH
                )
    
    # Create DataArrays
    sound_speed_da = xr.DataArray(
        sound_speeds,
        coords={'time1': pd.DatetimeIndex(insitu_times), 'channel': channels},
        dims=['time1', 'channel'],
        attrs={'units': 'm/s', 'long_name': 'Sound speed'},
    )
    
    absorption_da = xr.DataArray(
        absorptions,
        coords={'time1': pd.DatetimeIndex(insitu_times), 'channel': channels},
        dims=['time1', 'channel'],
        attrs={'units': 'dB/m', 'long_name': 'Sound absorption coefficient'},
    )
    
    logger.info(
        f"Built blended env_params: {n_times} time points, {n_channels} channels, "
        f"target_depth={target_depth}m"
    )
    
    return sound_speed_da, absorption_da


def print_profile_comparison(
    insitu_temp: float,
    insitu_sal: float,
    copernicus_profile: dict,
    frequency_hz: float = 38000,
    pH: float = 8.1,
):
    """Print a formatted comparison of in-situ vs Copernicus profiles."""
    
    print("=" * 70)
    print("BLENDED ENVIRONMENT PROFILE: In-Situ Surface + Copernicus Depth")
    print("=" * 70)
    print()
    
    # Build blended profile
    profile = build_blended_profile(
        insitu_temp=insitu_temp,
        insitu_sal=insitu_sal,
        insitu_depth=0.5,
        copernicus_profile=copernicus_profile,
        pH=pH,
    )
    
    print(f"{'Depth':>8}  {'Temp':>8}  {'Sal':>8}  {'c':>10}  {'α':>12}  {'Source':<10}")
    print(f"{'(m)':>8}  {'(°C)':>8}  {'(PSU)':>8}  {'(m/s)':>10}  {'(dB/m)':>12}")
    print("-" * 70)
    
    for i in range(len(profile['depth'])):
        d = profile['depth'][i]
        t = profile['temperature'][i]
        s = profile['salinity'][i]
        c = profile['sound_speed'][i]
        alpha = francois_garrison_absorption(frequency_hz, t, s, d, pH)
        src = profile['source'][i].upper()
        print(f"{d:8.1f}  {t:8.2f}  {s:8.2f}  {c:10.1f}  {alpha:12.6f}  {src:<10}")
    
    print()
    
    # Show effective values for different target depths
    print("Effective parameters for different target depths:")
    print(f"{'Target':>8}  {'c_eff':>10}  {'α_eff':>12}  {'Δc vs surface':<15}")
    print("-" * 50)
    
    surface_c = profile['sound_speed'][0]
    for target in [10, 50, 100, 200, 500]:
        if target <= profile['depth'][-1]:
            c_eff, alpha_eff = compute_depth_weighted_env_params(
                profile, target, frequency_hz, pH
            )
            delta_c = c_eff - surface_c
            print(f"{target:8.0f}m {c_eff:10.1f}  {alpha_eff:12.6f}  {delta_c:+8.1f} m/s")
