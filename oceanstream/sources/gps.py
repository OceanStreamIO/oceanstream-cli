"""Vehicle and ship tracks for raw files that carry no positions.

The pipeline reads positions from GPS parquet under ``--gps-dir`` (columns
``time``, ``latitude``, ``longitude``) when the raw files have no NMEA. This module
turns the tracks published next to such data into that form:

- Echoview-style ``*.gps.csv`` (``GPS_date,GPS_time,Latitude,Longitude``), which is
  what NCEI holds for the AFSC Saildrone missions, at 1 s or 5 min;
- ERDDAP ``tabledap`` tracks (e.g. PMEL's Saildrone datasets), 1 min, UTC;
- a generic CSV with time/latitude/longitude columns (e.g. a Sailbuoy logger).
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)

PMEL_ERDDAP = "https://data.pmel.noaa.gov/pmel/erddap"
TIME_NAMES = ("time", "gps_time", "datetime", "timestamp", "date_time", "utc", "time_utc")
LAT_NAMES = ("latitude", "lat", "lat_deg", "gps_lat")
LON_NAMES = ("longitude", "lon", "lng", "lon_deg", "gps_lon")


def _col(columns: list[str], names: tuple[str, ...]) -> str | None:
    lower = {c.lower().strip(): c for c in columns}
    for n in names:
        if n in lower:
            return lower[n]
    return None


def normalise(df: pd.DataFrame) -> pd.DataFrame:
    """``time`` (UTC), ``latitude``, ``longitude``: sorted, de-duplicated, no gaps in a row."""
    import pandas as pd  # noqa: PLC0415

    cols = list(df.columns)
    date_c = _col(cols, ("gps_date", "date"))
    time_c = _col(cols, TIME_NAMES)
    if date_c and time_c and date_c != time_c:
        t = pd.to_datetime(df[date_c].astype(str) + " " + df[time_c].astype(str), utc=True,
                           errors="coerce")
    elif time_c:
        t = pd.to_datetime(df[time_c], utc=True, errors="coerce")
    else:
        raise ValueError(f"No time column among {cols}")
    lat_c, lon_c = _col(cols, LAT_NAMES), _col(cols, LON_NAMES)
    if not (lat_c and lon_c):
        raise ValueError(f"No latitude/longitude columns among {cols}")
    out = pd.DataFrame({"time": t,
                        "latitude": pd.to_numeric(df[lat_c], errors="coerce"),
                        "longitude": pd.to_numeric(df[lon_c], errors="coerce")})
    out = out.dropna()
    out = out[(out.latitude.abs() <= 90) & (out.longitude.abs() <= 180)]  # noqa: PLR2004
    out = out[~((out.latitude == 0) & (out.longitude == 0))]
    return out.sort_values("time").drop_duplicates("time").reset_index(drop=True)


def read_csv(source: str | Path | IO[Any]) -> pd.DataFrame:
    """A track CSV (Echoview ``.gps.csv`` or any time/lat/lon table)."""
    import pandas as pd  # noqa: PLC0415

    return normalise(pd.read_csv(source))


def erddap_url(dataset_id: str, start: str, end: str, server: str = PMEL_ERDDAP) -> str:
    return (f"{server.rstrip('/')}/tabledap/{dataset_id}.csv?time,latitude,longitude"
            f"&time%3E={start}T00:00:00Z&time%3C={end}T23:59:59Z&latitude!=NaN")


def erddap_track(dataset_id: str, start: str, end: str, server: str = PMEL_ERDDAP,
                 timeout: float = 300) -> pd.DataFrame:
    """A dataset's track between *start* and *end* (inclusive dates) from ERDDAP."""
    import pandas as pd  # noqa: PLC0415
    import requests  # noqa: PLC0415

    r = requests.get(erddap_url(dataset_id, start, end, server), timeout=timeout)
    r.raise_for_status()
    # ERDDAP CSV has a units row under the header.
    df = pd.read_csv(io.StringIO(r.text), skiprows=[1])
    return normalise(df)


def write_parquet(track: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    track.to_parquet(path, index=False)
    return path


def describe(track: pd.DataFrame) -> dict[str, Any]:
    """Coverage of a track: span, fixes per day, the longest gap."""
    if track.empty:
        return {"fixes": 0}
    gaps = track.time.diff().dt.total_seconds().fillna(0)
    per_day = track.groupby(track.time.dt.strftime("%Y-%m-%d")).size()
    return {"fixes": len(track), "start": str(track.time.iloc[0]),
            "end": str(track.time.iloc[-1]), "days": len(per_day),
            "median_step_s": float(gaps[gaps > 0].median()) if (gaps > 0).any() else None,
            "longest_gap_h": round(float(gaps.max()) / 3600, 2),
            "lat_range": [float(track.latitude.min()), float(track.latitude.max())],
            "lon_range": [float(track.longitude.min()), float(track.longitude.max())]}
