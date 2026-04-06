"""ADCP coordinate transforms and ensemble averaging.

Converts raw beam-coordinate ADCP data to earth-coordinate velocity
profiles (u, v) with ensemble time-averaging.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import xarray as xr


def beam_to_earth(
    ds: xr.Dataset,
    transducer_depth: float = 7.0,
    corr_threshold: int = 64,
    pg_threshold: float = 0.5,
) -> xr.Dataset:
    """Convert raw beam-coordinate ADCP data to earth-coordinate velocities.

    Uses the beam-to-instrument matrix and orientation matrix provided
    by dolfyn to transform velocities. Applies basic QC: pings with
    low correlation are masked to NaN.

    Parameters
    ----------
    ds : xr.Dataset
        Raw dataset from ``rdi_reader.read_rdi`` with ``coord_sys='beam'``.
    transducer_depth : float
        Transducer depth below surface in meters (default 7.0 for R/V Kilo Moana).
    corr_threshold : int
        Minimum beam-averaged correlation (0-255). Bins below this are masked.
    pg_threshold : float
        Fraction of beams that must pass correlation check (0-1).

    Returns
    -------
    xr.Dataset
        Dataset with ``u`` (east), ``v`` (north), ``w`` (up) velocity
        components in earth coordinates, plus depth coordinate.
    """
    import xarray as xr

    coord_sys = ds.attrs.get("coord_sys", "")
    if coord_sys != "beam":
        raise ValueError(
            f"Expected coord_sys='beam', got '{coord_sys}'. "
            "Data may already be in earth coordinates."
        )

    vel = ds["vel"].values  # (4, range, time) — beam1-4
    b2i = ds["beam2inst_orientmat"].values  # (4, 4)
    orientmat = ds["orientmat"].values  # (earth=3, inst=3, time)

    # Step 1: beam → instrument
    vel_inst = np.einsum("ij,jrt->irt", b2i, vel)  # (4, range, time)

    # Step 2: instrument → earth using dolfyn's orientation matrix
    vel_earth = np.einsum("eit,irt->ert", orientmat, vel_inst[:3, :, :])  # (3, range, time)

    u_earth = vel_earth[0].astype(np.float32)  # east
    v_earth = vel_earth[1].astype(np.float32)  # north
    w_earth = vel_earth[2].astype(np.float32)  # up

    # Step 3: QC — mask bins with low correlation
    corr = ds["corr"].values  # (beam=4, range, time), uint8
    mean_corr = corr.mean(axis=0)  # (range, time)
    good_beams = (corr >= corr_threshold).sum(axis=0)  # (range, time)
    n_beams = corr.shape[0]
    bad_mask = (good_beams / n_beams) < pg_threshold

    u_earth[bad_mask] = np.nan
    v_earth[bad_mask] = np.nan
    w_earth[bad_mask] = np.nan

    # Also mask extreme error velocities (4th component of beam2inst)
    err_vel = np.abs(vel_inst[3])  # (range, time)
    err_threshold = 0.3  # m/s — screens out bad beam solutions
    err_mask = err_vel > err_threshold
    u_earth[err_mask] = np.nan
    v_earth[err_mask] = np.nan
    w_earth[err_mask] = np.nan

    # Compute depth from range + transducer depth
    range_m = ds["range"].values
    depth = range_m + transducer_depth

    time_coord = ds["time"].values

    out = xr.Dataset(
        {
            "u": (["range", "time"], u_earth, {"units": "m s-1", "long_name": "Eastward velocity"}),
            "v": (["range", "time"], v_earth, {"units": "m s-1", "long_name": "Northward velocity"}),
            "w": (["range", "time"], w_earth, {"units": "m s-1", "long_name": "Upward velocity"}),
            "amp": (["beam", "range", "time"], ds["amp"].values, {"long_name": "Signal amplitude"}),
            "corr": (["beam", "range", "time"], ds["corr"].values, {"long_name": "Correlation"}),
            "heading": (["time"], ds["heading"].values, {"units": "degrees", "long_name": "Heading"}),
            "pitch": (["time"], ds["pitch"].values, {"units": "degrees", "long_name": "Pitch"}),
            "roll": (["time"], ds["roll"].values, {"units": "degrees", "long_name": "Roll"}),
            "temp": (["time"], ds["temp"].values, {"units": "degree_C", "long_name": "Transducer temperature"}),
            "pressure": (["time"], ds["pressure"].values, {"units": "dbar", "long_name": "Pressure"}),
        },
        coords={
            "time": time_coord,
            "range": range_m,
            "depth": (["range"], depth, {"units": "meter", "long_name": "Depth"}),
            "beam": ds["beam"].values,
        },
        attrs={
            "inst_make": ds.attrs.get("inst_make", ""),
            "inst_model": ds.attrs.get("inst_model", ""),
            "freq": ds.attrs.get("freq", 0),
            "n_beams": ds.attrs.get("n_beams", 0),
            "coord_sys": "earth",
            "transducer_depth": transducer_depth,
            "corr_threshold": corr_threshold,
        },
    )
    return out


def ensemble_average(
    ds: xr.Dataset,
    interval_seconds: float = 120.0,
) -> xr.Dataset:
    """Time-average earth-coordinate velocities into ensembles.

    Parameters
    ----------
    ds : xr.Dataset
        Earth-coordinate dataset from ``beam_to_earth``.
    interval_seconds : float
        Averaging interval in seconds (default 120 = 2 minutes,
        matching UHDAS convention for this cruise).

    Returns
    -------
    xr.Dataset
        Averaged dataset with reduced time dimension.
    """
    import xarray as xr
    import pandas as pd

    interval = pd.Timedelta(seconds=interval_seconds)

    # Group by time bins
    time_bins = pd.cut(
        pd.DatetimeIndex(ds["time"].values),
        bins=pd.date_range(
            start=ds["time"].values[0],
            end=ds["time"].values[-1] + interval,
            freq=interval,
        ),
    )

    # Compute bin centers and averages
    bin_edges = time_bins.categories
    results = {
        "u": [], "v": [], "w": [],
        "amp_mean": [],
        "heading": [], "temp": [],
        "num_pings": [],
    }
    time_centers = []

    for interval_bin in bin_edges:
        mask = (ds["time"].values >= interval_bin.left.to_datetime64()) & (
            ds["time"].values < interval_bin.right.to_datetime64()
        )
        n_pings = int(mask.sum())
        if n_pings == 0:
            continue

        time_centers.append(interval_bin.mid.to_datetime64())
        results["num_pings"].append(n_pings)

        sub = ds.isel(time=mask)
        results["u"].append(sub["u"].mean(dim="time").values)
        results["v"].append(sub["v"].mean(dim="time").values)
        results["w"].append(sub["w"].mean(dim="time").values)
        results["amp_mean"].append(
            sub["amp"].mean(dim=["beam", "time"]).values.astype(np.float32)
        )
        results["heading"].append(float(sub["heading"].mean().values))
        results["temp"].append(float(sub["temp"].mean().values))

    n_ens = len(time_centers)
    n_range = ds.sizes["range"]
    depth = ds["depth"].values

    out = xr.Dataset(
        {
            "u": (
                ["time", "depth_cell"],
                np.array(results["u"], dtype=np.float32).reshape(n_ens, n_range),
                {"units": "meter second-1", "long_name": "Zonal velocity component"},
            ),
            "v": (
                ["time", "depth_cell"],
                np.array(results["v"], dtype=np.float32).reshape(n_ens, n_range),
                {"units": "meter second-1", "long_name": "Meridional velocity component"},
            ),
            "amp": (
                ["time", "depth_cell"],
                np.array(results["amp_mean"], dtype=np.float32).reshape(n_ens, n_range),
                {"long_name": "Received signal strength"},
            ),
            "heading": (
                ["time"],
                np.array(results["heading"], dtype=np.float32),
                {"units": "degrees", "long_name": "Ship heading"},
            ),
            "tr_temp": (
                ["time"],
                np.array(results["temp"], dtype=np.float32),
                {"units": "Celsius", "long_name": "ADCP transducer temperature"},
            ),
            "num_pings": (
                ["time"],
                np.array(results["num_pings"], dtype=np.int16),
                {"long_name": "Number of pings averaged per ensemble"},
            ),
            "depth": (
                ["time", "depth_cell"],
                np.tile(depth, (n_ens, 1)).astype(np.float32),
                {"units": "meter", "long_name": "Depth"},
            ),
        },
        coords={
            "time": np.array(time_centers, dtype="datetime64[ns]"),
        },
        attrs={
            **ds.attrs,
            "ensemble_interval_seconds": interval_seconds,
        },
    )
    return out
