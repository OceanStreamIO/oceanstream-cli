"""Sv computation for Nortek Signature AD2CP echosounder data.

Converts raw echo amplitude from ``.ad2cp`` files to volume
backscattering strength (Sv) using the instrument's internal TVG
and factory calibration offsets.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import pandas as pd
    import xarray as xr

logger = logging.getLogger("oceanstream")


def compute_sv(
    ds: "xr.Dataset",
    absorption: float | None = None,
    salinity: float = 35.0,
) -> "xr.Dataset":
    """Compute Sv from AD2CP echosounder echo amplitude.

    The Nortek Signature echosounder applies TVG (Time-Varied Gain)
    internally before recording.  The raw ``echo_amplitude`` values
    are proportional to signal level in 0.01 dB units, with geometric
    spreading and a nominal absorption already compensated.

    This function converts raw counts to Sv by applying the per-channel
    calibration offsets stored in the file's ``CALECHOGET`` record.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset from :func:`~oceanstream.adcp.ad2cp_reader.read_ad2cp`
        containing ``echo_amplitude``, ``sound_speed``, ``temperature``,
        ``pressure`` and calibration attributes.
    absorption : float or None
        Override absorption coefficient in dB/m.  If ``None``, absorption
        is estimated from temperature, salinity and frequency using the
        Francois-Garrison formula.
    salinity : float
        Salinity in PSU, used for absorption estimation when *absorption*
        is ``None``.  Default 35.0 (typical open ocean).

    Returns
    -------
    xr.Dataset
        Copy of the input with an added ``Sv`` variable
        (dims: frequency × ping_time × range_sample) in dB re 1 m⁻¹.
    """
    import xarray as xr

    amp = ds["echo_amplitude"].values  # (freq, ping, range) uint16
    cal_offsets = ds.attrs.get("cal_offsets_db", [0.0] * amp.shape[0])
    range_m = ds["range_sample"].values  # (range,)

    n_freqs = amp.shape[0]
    sv = np.full_like(amp, np.nan, dtype=np.float32)

    for fi in range(n_freqs):
        # Convert raw counts to dB echo level
        el = amp[fi].astype(np.float32) * 0.01

        cal = cal_offsets[fi] if fi < len(cal_offsets) else 0.0
        sv[fi] = el + cal

    # Mask cells below the seafloor using bottom-track depth
    if "bottom_depth" in ds:
        bd = ds["bottom_depth"].values  # (ping,)
        for pi in range(sv.shape[1]):
            if not np.isnan(bd[pi]):
                below = range_m >= bd[pi]
                sv[:, pi, below] = np.nan

    ds_out = ds.copy()
    ds_out["Sv"] = xr.DataArray(
        sv,
        dims=["frequency", "ping_time", "range_sample"],
        attrs={
            "long_name": "Volume backscattering strength",
            "units": "dB re 1 m-1",
            "comment": (
                "Sv with instrument TVG applied. Calibration offsets from "
                "CALECHOGET applied per channel. For absolute Sv, a "
                "system constant from the calibration certificate is "
                "needed: Sv_abs = Sv - C_system."
            ),
        },
    )

    logger.info(
        "AD2CP Sv: %d freq, %d pings, Sv range %.1f to %.1f dB%s",
        n_freqs,
        amp.shape[1],
        float(np.nanmin(sv)),
        float(np.nanmax(sv)),
        (
            f", clipped at seafloor ~{float(np.nanmean(ds['bottom_depth'].values)):.0f} m"
            if "bottom_depth" in ds and not np.all(np.isnan(ds["bottom_depth"].values))
            else ""
        ),
    )

    return ds_out


def _francois_garrison_absorption(
    frequency_khz: float,
    temperature_c: float,
    salinity_psu: float,
    depth_m: float,
) -> float:
    """Estimate acoustic absorption using Francois & Garrison (1982).

    Parameters
    ----------
    frequency_khz : float
        Acoustic frequency in kHz.
    temperature_c : float
        Water temperature in °C.
    salinity_psu : float
        Salinity in PSU.
    depth_m : float
        Depth in meters.

    Returns
    -------
    float
        Absorption coefficient in dB/m.
    """
    f = frequency_khz
    T = temperature_c
    S = salinity_psu
    D = depth_m / 1000.0  # km

    # Boric acid contribution
    A1 = 8.86 / (10.0 ** (0.78 * np.sqrt(max(S, 0)) - 5.0)) * 10.0 ** (0.002 * T)
    f1 = 2.8 * np.sqrt(max(S, 0) / 35.0) * 10.0 ** (4.0 - 1245.0 / (T + 273.0))
    P1 = 1.0

    # MgSO4 contribution
    A2 = 21.44 * max(S, 0) / (10.0 ** (0.78 * max(S, 0) - 5.0)) if S > 0 else 0.0
    f2 = (8.17 * 10.0 ** (8.0 - 1990.0 / (T + 273.0))) / (1.0 + 0.0018 * (S - 35.0))
    P2 = 1.0 - 1.37e-4 * D + 6.2e-9 * D**2

    # Pure water contribution
    A3 = 3.964e-4 - 1.146e-5 * T + 1.45e-7 * T**2 - 6.5e-10 * T**3 if T >= 20 else (
        4.937e-4 - 2.590e-5 * T + 9.11e-7 * T**2 - 1.50e-8 * T**3
    )
    P3 = 1.0 - 3.83e-5 * D + 4.9e-10 * D**2

    alpha = (
        A1 * P1 * f1 * f**2 / (f1**2 + f**2)
        + A2 * P2 * f2 * f**2 / (f2**2 + f**2)
        + A3 * P3 * f**2
    )

    return alpha / 1000.0  # convert dB/km to dB/m


def ad2cp_sv_to_dataframe(ds: "xr.Dataset") -> "pd.DataFrame":
    """Flatten AD2CP Sv dataset to a tabular DataFrame.

    One row per (ping_time, range_sample, frequency).

    Parameters
    ----------
    ds : xr.Dataset
        Dataset with ``Sv`` variable from :func:`compute_sv`.

    Returns
    -------
    pd.DataFrame
        Columns: ``time``, ``depth``, ``frequency_khz``, ``Sv``,
        ``temperature``, ``sound_speed``, ``heading``, ``num_pings``.
    """
    import pandas as pd

    if "Sv" not in ds:
        raise ValueError("Dataset has no 'Sv' variable — run compute_sv() first")

    rows = []
    times = ds["ping_time"].values
    range_m = ds["range_sample"].values
    freqs = ds["frequency"].values
    sv = ds["Sv"].values  # (freq, ping, range)

    for fi, freq in enumerate(freqs):
        for ti in range(len(times)):
            t = pd.Timestamp(times[ti], tz="UTC")
            temp = float(ds["temperature"].values[ti])
            sos = float(ds["sound_speed"].values[ti])
            hdg = float(ds["heading"].values[ti])

            for ri in range(len(range_m)):
                sv_val = float(sv[fi, ti, ri])
                if np.isnan(sv_val):
                    continue
                rows.append({
                    "time": t,
                    "depth": float(range_m[ri]),
                    "frequency_khz": int(freq),
                    "Sv": sv_val,
                    "temperature": temp,
                    "sound_speed": sos,
                    "heading": hdg,
                })

    return pd.DataFrame(rows)
