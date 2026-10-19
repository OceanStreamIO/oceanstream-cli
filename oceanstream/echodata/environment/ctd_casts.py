"""Sound speed and absorption from CTD casts, for Sv from ship-based echosounders.

A research cruise's CTD casts describe the water column the echosounder looked through. For
each cast this module integrates, on a 1 m depth grid, the slowness 1/c from the Mackenzie
(1981) sound speed and the Francois-Garrison (1982) absorption at each echosounder
frequency. Sv needs both as means along the acoustic path: the harmonic-mean sound speed
turns two-way travel time into range, and the absorption loss at range r is 2 r times the
absorption averaged from the transducer to r, not the value at r.

echopype's ``compute_Sv`` takes one sound speed and one absorption per channel and ping. The
pipeline gives it each channel's means over its whole range, from the casts either side of
each ping in time (:func:`channel_environment`), then corrects every sample by
``2 [A(d + r) - A(d)] - 2 r a_used``, where A is the integrated absorption and d the
transducer depth (:func:`absorption_correction`). On RR2205 a single value per channel
left up to 2.7 dB of error mid-range at 200 kHz, because absorption there differs strongly
between the warm surface layer and the cold water below.

Casts come from CCHDO's CF netCDF CTD product (``<expocode>_ctd.nc``, N_PROF casts by
N_LEVELS pressure levels). pH, which only the boric-acid term below about 10 kHz depends
on, is taken as 8.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from oceanstream.echodata.environment.absorption import francois_garrison_absorption
from oceanstream.echodata.environment.sound_speed import mackenzie_sound_speed

if TYPE_CHECKING:
    from pathlib import Path

    import xarray as xr

#: WOCE CTD quality flags marking a value unusable: questionable, bad, not reported, not sampled.
BAD_FLAGS = (3, 4, 5, 9)
DEFAULT_PH = 8.0
#: A cast this close to the seabed covers the whole water column there.
BOTTOM_MARGIN_M = 100.0


@dataclass
class CastProfiles:
    """Depth-integrated slowness and absorption for a set of CTD casts.

    ``slowness[i, k]`` integrates 1/c from the surface to ``depth[k]`` in cast i (s/m * m),
    and ``absorption[f][i, k]`` integrates the absorption at frequency f (dB). Means over a
    depth interval follow from differences. Below a cast's deepest level its last
    temperature and salinity are held.
    """

    time: np.ndarray
    depth: np.ndarray
    max_depth: np.ndarray
    bottom: np.ndarray
    slowness: np.ndarray
    absorption: dict[float, np.ndarray]
    source: str = ""

    def usable(self, need_m: float) -> np.ndarray:
        """Casts that reach *need_m*, or the seabed where it is shallower."""
        reaches_bottom = np.isfinite(self.bottom) & (
            self.max_depth >= self.bottom - BOTTOM_MARGIN_M
        )
        return (self.max_depth >= need_m) | reaches_bottom


def _cumulative(values: np.ndarray, dz: float) -> np.ndarray:
    """Trapezoidal running integral along the last axis, starting at 0."""
    steps = (values[..., 1:] + values[..., :-1]) * (dz / 2)
    return np.concatenate([np.zeros(values.shape[:-1] + (1,)), np.cumsum(steps, axis=-1)], axis=-1)


def _valid(values: np.ndarray, flags: np.ndarray | None) -> np.ndarray:
    ok = np.isfinite(values)
    if flags is not None:
        ok &= ~np.isin(np.nan_to_num(flags, nan=9), BAD_FLAGS)
    return ok


def cast_profiles(  # noqa: PLR0913 - keyword-only grid and pH settings
    casts: xr.Dataset,
    frequencies: list[float],
    max_depth_m: float,
    *,
    dz: float = 1.0,
    ph: float = DEFAULT_PH,
    source: str = "",
) -> CastProfiles:
    """Integrated slowness and absorption for every cast in a CCHDO CF CTD dataset."""
    import gsw  # noqa: PLC0415 - optional echodata dependency

    grid = np.arange(0.0, max_depth_m + dz, dz)
    order = np.argsort(casts["time"].values)
    temp = casts["ctd_temperature"].values[order]
    sal = casts["ctd_salinity"].values[order]
    pres = casts["pressure"].values[order]
    tq = casts["ctd_temperature_qc"].values[order] if "ctd_temperature_qc" in casts else None
    sq = casts["ctd_salinity_qc"].values[order] if "ctd_salinity_qc" in casts else None
    lat = casts["latitude"].values[order]
    bottom = (casts["btm_depth"].values[order].astype(float)
              if "btm_depth" in casts else np.full(order.size, np.nan))

    keep, max_depth, slowness = [], [], []
    absorption: dict[float, list] = {float(f): [] for f in frequencies}
    for i in range(order.size):
        ok = (_valid(temp[i], None if tq is None else tq[i])
              & _valid(sal[i], None if sq is None else sq[i]) & np.isfinite(pres[i]))
        if ok.sum() < 2:  # noqa: PLR2004 - a profile needs two levels
            continue
        z = -gsw.z_from_p(pres[i][ok], lat[i])
        z, first = np.unique(z, return_index=True)
        t = np.interp(grid, z, temp[i][ok][first])
        s = np.interp(grid, z, sal[i][ok][first])
        keep.append(i)
        max_depth.append(float(z.max()))
        slowness.append(_cumulative(1.0 / mackenzie_sound_speed(t, s, grid), dz))
        for f, rows in absorption.items():
            alpha = francois_garrison_absorption(
                frequency_hz=f, temperature=t, salinity=s, depth=grid, pH=ph
            )
            rows.append(_cumulative(np.asarray(alpha, dtype=float), dz))
    if not keep:
        raise ValueError(f"No usable CTD casts in {source or 'the dataset'}.")
    keep_idx = np.asarray(keep)
    return CastProfiles(
        time=casts["time"].values[order][keep_idx].astype("datetime64[ns]"),
        depth=grid,
        max_depth=np.asarray(max_depth),
        bottom=bottom[keep_idx],
        slowness=np.asarray(slowness),
        absorption={f: np.asarray(v) for f, v in absorption.items()},
        source=source,
    )


def load_cchdo_ctd(
    path: str | Path,
    frequencies: list[float],
    max_depth_m: float,
    dz: float = 1.0,
    ph: float = DEFAULT_PH,
) -> CastProfiles:
    """:func:`cast_profiles` for a CCHDO ``*_ctd.nc`` file."""
    import xarray as xr  # noqa: PLC0415 - kept out of the module import

    with xr.open_dataset(path) as casts:
        return cast_profiles(casts.load(), frequencies, max_depth_m, dz=dz, ph=ph,
                             source=str(path))


def _bracket(cast_times: np.ndarray, times: np.ndarray):
    """Indices of the casts before and after each time, and the weight of the later one.

    Times before the first cast or after the last take that cast alone.
    """
    tc = cast_times.astype("datetime64[ns]").astype(np.int64)
    tp = np.asarray(times, dtype="datetime64[ns]").astype(np.int64)
    after = np.clip(np.searchsorted(tc, tp), 0, tc.size - 1)
    before = np.clip(after - 1, 0, tc.size - 1)
    span = (tc[after] - tc[before]).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        weight = np.where(span > 0, (tp - tc[before]) / span, 0.0)
    return before, after, np.clip(weight, 0.0, 1.0)


def _at(profiles: CastProfiles, table: np.ndarray, depth: float | np.ndarray) -> np.ndarray:
    """Integrated quantity of every cast in *table* at *depth* (scalar: per cast)."""
    return np.array([np.interp(depth, profiles.depth, row) for row in table])


def _selection(profiles: CastProfiles, need_m: float):
    sel = profiles.usable(need_m)
    if not sel.any():
        raise ValueError(
            f"No CTD cast in {profiles.source or 'the cast set'} reaches {need_m:.0f} m or the "
            "seabed. Supply full-depth casts, or limit the channel's range."
        )
    return sel


def channel_environment(
    profiles: CastProfiles,
    frequency: float,
    ping_times: np.ndarray,
    max_range_m: float,
    transducer_depth_m: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Sound speed (m/s) and absorption (dB/m) per ping, averaged over one channel's range.

    Each is the mean from the transducer to ``transducer_depth_m + max_range_m`` in the
    casts either side of the ping, interpolated linearly in time.
    """
    need = transducer_depth_m + max_range_m
    sel = _selection(profiles, need)
    slow = profiles.slowness[sel]
    absorb = profiles.absorption[float(frequency)][sel]
    top = transducer_depth_m
    sound_speed = max_range_m / (_at(profiles, slow, need) - _at(profiles, slow, top))
    absorption = (_at(profiles, absorb, need) - _at(profiles, absorb, top)) / max_range_m
    before, after, w = _bracket(profiles.time[sel], ping_times)
    return ((1 - w) * sound_speed[before] + w * sound_speed[after],
            (1 - w) * absorption[before] + w * absorption[after])


def absorption_correction(  # noqa: PLR0913 - the per-ping inputs of one channel
    profiles: CastProfiles,
    frequency: float,
    ping_times: np.ndarray,
    echo_range: np.ndarray,
    absorption_used: np.ndarray,
    *,
    transducer_depth_m: float = 0.0,
) -> np.ndarray:
    """dB to add to Sv so its absorption loss uses the mean absorption to each sample.

    *echo_range* is (ping, sample) in metres and *absorption_used* (ping,) the dB/m Sv
    was computed with. Returns ``2 [A(d + r) - A(d)] - 2 r a_used``, NaN where the range is.
    """
    echo_range = np.asarray(echo_range, dtype=float)
    finite = np.isfinite(echo_range)
    need = transducer_depth_m + (float(np.nanmax(echo_range)) if finite.any() else 0.0)
    sel = _selection(profiles, need)
    table = profiles.absorption[float(frequency)][sel]
    before, after, w = _bracket(profiles.time[sel], ping_times)
    depth = transducer_depth_m + np.where(finite, echo_range, 0.0)
    path_before = np.empty_like(echo_range)
    path_after = np.empty_like(echo_range)
    for i in np.unique(np.concatenate([before, after])):
        a_top = np.interp(transducer_depth_m, profiles.depth, table[i])
        for rows, path in ((before == i, path_before), (after == i, path_after)):
            if rows.any():
                path[rows] = np.interp(depth[rows], profiles.depth, table[i]) - a_top
    path = (1 - w)[:, None] * path_before + w[:, None] * path_after
    corr = 2 * path - 2 * echo_range * np.asarray(absorption_used, dtype=float)[:, None]
    return np.where(finite, corr, np.nan)
