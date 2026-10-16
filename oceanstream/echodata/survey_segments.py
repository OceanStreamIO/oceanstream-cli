"""Split a ship survey into station and transit segments from its navigation.

On a station the ship holds position for hours (CTD casts, thrusters, winch), and the
echosounder sees different noise and a different picture: a stationary time series rather
than a transect. Denoising runs per segment, with each segment's own preset, and the two
kinds of segment become separate datasets.

Segments come from the ship's speed over ground, not from a cruise event log, because
event logs are incomplete (RR2205's misses about a third of the EK80 recording). Log
events, when available, only label the segments with station and cast numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    import pandas as pd

STATION = "station"
TRANSIT = "transit"
TRANSITION = "transition"


@dataclass(frozen=True)
class SegmentRules:
    """Thresholds for classifying navigation fixes. Speeds are over ground."""

    #: At or below this the ship is holding position (0.5 m/s is about 1 knot).
    station_speed_mps: float = 0.5
    #: At or above this the ship is under way (2 m/s is about 4 knots).
    transit_speed_mps: float = 2.0
    #: Rolling median applied to speed first, so single noisy fixes do not split a segment.
    smooth_minutes: float = 5.0
    #: Station and transit runs shorter than this become transitions.
    min_station_minutes: float = 20.0
    min_transit_minutes: float = 20.0
    #: A navigation gap longer than this ends a segment.
    max_gap_minutes: float = 10.0


def sun_elevation_deg(times: pd.DatetimeIndex, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Solar elevation in degrees (NOAA approximation, about 0.5 degree accuracy).

    *times* are UTC; *lat* and *lon* are degrees, east positive.
    """
    import numpy as np

    doy = times.dayofyear.to_numpy()
    hours = (times.hour + times.minute / 60 + times.second / 3600).to_numpy()
    gamma = 2 * math.pi / 365 * (doy - 1 + (hours - 12) / 24)
    decl = (0.006918 - 0.399912 * np.cos(gamma) + 0.070257 * np.sin(gamma)
            - 0.006758 * np.cos(2 * gamma) + 0.000907 * np.sin(2 * gamma)
            - 0.002697 * np.cos(3 * gamma) + 0.00148 * np.sin(3 * gamma))
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(gamma) - 0.032077 * np.sin(gamma)
                       - 0.014615 * np.cos(2 * gamma) - 0.040849 * np.sin(2 * gamma))
    solar_minutes = hours * 60 + eqtime + 4 * np.asarray(lon)
    hour_angle = np.radians(solar_minutes / 4 - 180)
    lat_r = np.radians(np.asarray(lat))
    cos_zenith = np.sin(lat_r) * np.sin(decl) + np.cos(lat_r) * np.cos(decl) * np.cos(hour_angle)
    return 90 - np.degrees(np.arccos(np.clip(cos_zenith, -1, 1)))


def _haversine_nmi(lat: np.ndarray, lon: np.ndarray) -> float:
    import numpy as np

    if len(lat) < 2:  # noqa: PLR2004
        return 0.0
    p1, p2 = np.radians(lat[:-1]), np.radians(lat[1:])
    dlat = p2 - p1
    dlon = np.radians(np.asarray(lon[1:]) - np.asarray(lon[:-1]))
    a = np.sin(dlat / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlon / 2) ** 2
    return float(np.sum(2 * 3440.065 * np.arcsin(np.sqrt(a))))


def _runs(labels: np.ndarray) -> list[tuple[int, int]]:
    """Half-open index ranges of consecutive equal labels."""
    import numpy as np

    edges = np.flatnonzero(labels[1:] != labels[:-1]) + 1
    starts = np.concatenate([[0], edges])
    ends = np.concatenate([edges, [len(labels)]])
    return list(zip(starts.tolist(), ends.tolist(), strict=True))


def classify_fixes(nav: pd.DataFrame, rules: SegmentRules = SegmentRules()) -> pd.Series:
    """One label per navigation fix: station, transit or transition.

    *nav* needs ``time`` (UTC) and ``speed_mps``, sorted by time.
    """
    import numpy as np
    import pandas as pd

    speed = (nav.set_index("time")["speed_mps"]
             .rolling(pd.Timedelta(minutes=rules.smooth_minutes), center=True).median())
    labels = np.where(speed <= rules.station_speed_mps, STATION,
                      np.where(speed >= rules.transit_speed_mps, TRANSIT, TRANSITION))
    times = nav["time"].to_numpy()

    # Runs too short to stand alone become transitions, then neighbours are re-merged.
    minimum = {STATION: rules.min_station_minutes, TRANSIT: rules.min_transit_minutes}
    for start, end in _runs(labels):
        kind = labels[start]
        if kind in minimum:
            minutes = (times[end - 1] - times[start]) / np.timedelta64(1, "m")
            if minutes < minimum[kind]:
                labels[start:end] = TRANSITION
    return pd.Series(labels, index=nav.index, name="kind")


def build_segments(
    nav: pd.DataFrame,
    rules: SegmentRules = SegmentRules(),
    events: pd.DataFrame | None = None,
    prefix: str = "",
) -> pd.DataFrame:
    """The survey's segments, one row each.

    *nav* needs ``time``, ``latitude``, ``longitude`` and ``speed_mps``. *events*, when
    given, needs ``time``, ``station`` and ``cast`` for each CTD deployment; station
    segments get the stations and casts that started inside them.
    """
    import numpy as np
    import pandas as pd

    nav = nav.sort_values("time").reset_index(drop=True)
    kinds = classify_fixes(nav, rules).to_numpy()
    times = nav["time"]

    # A navigation gap ends a segment even when the label does not change.
    gap = times.diff() > pd.Timedelta(minutes=rules.max_gap_minutes)
    block = np.cumsum(gap.to_numpy() | np.r_[False, kinds[1:] != kinds[:-1]])

    elevation = sun_elevation_deg(pd.DatetimeIndex(times), nav["latitude"].to_numpy(),
                                  nav["longitude"].to_numpy())
    groups = list(nav.groupby(block).groups.values())
    rows = []
    for number, idx in enumerate(groups, start=1):
        part = nav.loc[idx]
        start, end = part["time"].iloc[0], part["time"].iloc[-1]
        # Contiguous segments share a boundary, so every ping between fixes has a segment.
        if number < len(groups) and not gap.iloc[groups[number][0]]:
            end = times.iloc[groups[number][0]]
        row = {
            "segment_id": f"{prefix}{number:04d}",
            "kind": kinds[idx[0]],
            "start": start,
            "end": end,
            "minutes": round((end - start) / pd.Timedelta(minutes=1), 1),
            "fixes": len(part),
            "lat_start": part["latitude"].iloc[0], "lon_start": part["longitude"].iloc[0],
            "lat_end": part["latitude"].iloc[-1], "lon_end": part["longitude"].iloc[-1],
            "lat_mean": part["latitude"].mean(), "lon_mean": part["longitude"].mean(),
            "distance_nmi": round(_haversine_nmi(part["latitude"].to_numpy(),
                                                 part["longitude"].to_numpy()), 2),
            "speed_mps_median": round(float(part["speed_mps"].median()), 2),
            "daylight_fraction": round(float((elevation[idx] > 0).mean()), 2),
        }
        rows.append(row)
    segments = pd.DataFrame(rows)

    if events is not None and not events.empty and not segments.empty:
        ev = events.sort_values("time")
        stations, casts = [], []
        for _, seg in segments.iterrows():
            inside = ev[(ev["time"] >= seg["start"]) & (ev["time"] <= seg["end"])]
            labels = {str(s) for s in inside["station"] if str(s) != "nan"}
            stations.append(",".join(sorted(labels)))
            casts.append(len(inside))
        segments["stations"] = stations
        segments["casts"] = casts
    return segments


def assign_segments(ping_times: np.ndarray, segments: pd.DataFrame) -> np.ndarray:
    """The segment id of each ping, or an empty string for pings outside every segment."""
    import numpy as np

    starts = segments["start"].to_numpy(dtype="datetime64[ns]")
    ends = segments["end"].to_numpy(dtype="datetime64[ns]")
    pings = np.asarray(ping_times, dtype="datetime64[ns]")
    i = np.searchsorted(starts, pings, side="right") - 1
    ok = (i >= 0) & (pings <= ends[np.clip(i, 0, None)])
    ids = segments["segment_id"].to_numpy(dtype=object)
    return np.where(ok, ids[np.clip(i, 0, None)], "")
