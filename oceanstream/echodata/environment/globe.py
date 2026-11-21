"""Water columns along a GPS track from the OceanStream globe, for Sv.

The globe (arco-3d) serves temperature and salinity columns from Copernicus Marine ocean
models, corrected where measured profiles (Argo, CTD, XBT, gliders, moorings) are near and
replaced by a measured profile where one is close enough. ``POST /api/env/track`` turns
positions into one column per position, and with ``format=cchdo`` returns them in the
layout of CCHDO's CTD files, which :func:`oceanstream.echodata.environment.ctd_casts.
load_cchdo_ctd` reads. Sv then gets the same per-sample correction as with real CTD casts.

The track is sent as one position per UTC hour, the mean of that hour's fixes (as the globe
itself averages raw fixes), timed at the middle of the hour, which the mean stands for. The
model is the reanalysis (GLORYS12) wherever it covers the day, else the analysis and
forecast, unless asked for. An API key, read from
``OCEANSTREAM_GLOBE_API_KEY``, lets the globe read Copernicus Marine directly where its
mirror does not reach; without one only mirrored waters are served.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

    import pandas as pd

DEFAULT_URL = "https://globe.oceanstream.io"
URL_ENV = "OCEANSTREAM_GLOBE_URL"
KEY_ENV = "OCEANSTREAM_GLOBE_API_KEY"
MODELS = ("auto", "glorys", "anfc")
#: Attempts at a request the globe refused for the moment (rate limit, restart, timeout).
ATTEMPTS = 4
TIMEOUT_S = 300
#: ``cast_source`` flag values in the globe's casts file.
CAST_KINDS = {0: "model", 1: "model corrected by nearby profiles", 2: "measured profile"}
#: Within this of (0, 0) (degrees) a fix is a receiver without one.
NO_FIX_DEG = 1e-6


class GlobeError(RuntimeError):
    """The globe could not give water columns for the track."""


@dataclass
class GlobeSettings:
    """Where the globe is and what to ask it for."""

    url: str = ""
    model: str = "auto"
    insitu: bool = True
    api_key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        self.url = (self.url or os.environ.get(URL_ENV) or DEFAULT_URL).rstrip("/")
        self.api_key = self.api_key or os.environ.get(KEY_ENV, "")
        if self.model not in MODELS:
            raise ValueError(f"globe model {self.model!r}: use one of {', '.join(MODELS)}")


@dataclass
class TrackCasts:
    """The casts file the globe wrote for a track, and what it holds."""

    path: Path
    model: str
    source: str
    kinds: dict[str, int]

    def describe(self, url: str) -> str:
        """One line for the Sv store's ``environment`` attribute."""
        n = sum(self.kinds.values())
        parts = ", ".join(f"{count} {kind}" for kind, count in self.kinds.items() if count)
        return (
            f"Sound speed and absorption from {n} water columns along the track from the "
            f"OceanStream globe ({url}/api/env/track, {self.source}; {parts}); Mackenzie "
            "(1981) sound speed and Francois-Garrison (1982) absorption, pH 8.0"
        )


def hourly_points(fixes: pd.DataFrame) -> list[dict]:
    """One position per UTC hour from ``dt``/``lat``/``lon`` fixes, as the globe makes them.

    Each is the mean of the hour's fixes at the middle of the hour, longitudes averaged as
    directions so that a track across 180° stays where it is. (0, 0) is a failed fix.
    """
    import pandas as pd  # noqa: PLC0415

    df = fixes[["dt", "lat", "lon"]].dropna()
    df = df[~((df["lat"].abs() < NO_FIX_DEG) & (df["lon"].abs() < NO_FIX_DEG))]
    if df.empty:
        return []
    times = pd.to_datetime(df["dt"], utc=True)
    rad = np.radians(df["lon"].to_numpy(float))
    hours = (
        pd.DataFrame(
            {
                "hour": times.dt.floor("h").to_numpy(),
                "lat": df["lat"].to_numpy(float),
                "sin": np.sin(rad),
                "cos": np.cos(rad),
            }
        )
        .groupby("hour")
        .mean()
    )
    return [
        {
            "time": (hour + pd.Timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            "lat": round(float(row.lat), 6),
            "lon": round(float(np.degrees(np.arctan2(row.sin, row.cos))), 6),
        }
        for hour, row in hours.iterrows()
    ]


def _request(req: urllib.request.Request) -> bytes:
    """The response body, retrying what the globe refuses only for the moment."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - https URL
                return resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if e.code not in (429, 502, 503, 504) or attempt == ATTEMPTS:
                raise GlobeError(f"{req.full_url}: HTTP {e.code}: {detail}") from e
            wait = float(e.headers.get("Retry-After") or 10 * attempt)
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == ATTEMPTS:
                raise GlobeError(
                    f"{req.full_url}: {e}. The globe must be reachable from where "
                    "the pipeline runs (not from MareNostrum 5)."
                ) from e
            wait = 10.0 * attempt
        time.sleep(min(wait, 120.0))
    raise AssertionError("unreachable")


def _headers(settings: GlobeSettings) -> dict[str, str]:
    headers = {"Accept": "application/json, application/x-netcdf"}
    if settings.api_key:
        headers["X-API-Key"] = settings.api_key
    return headers


def model_for(day: dt.date, settings: GlobeSettings) -> str:
    """The model to ask for: the reanalysis where it covers *day*, else the analysis."""
    if settings.model != "auto":
        return settings.model
    req = urllib.request.Request(f"{settings.url}/api/env/sources", headers=_headers(settings))
    sources = json.loads(_request(req))
    for model in sources.get("models", []):
        if model.get("model") == "glorys" and model.get("coverage"):
            start, end = (dt.date.fromisoformat(d) for d in model["coverage"])
            if start <= day <= end:
                return "glorys"
    return "anfc"


def track_casts(fixes: pd.DataFrame, path: Path, settings: GlobeSettings) -> TrackCasts:
    """Ask the globe for the water columns along *fixes* and write them to *path*."""
    import xarray as xr  # noqa: PLC0415

    points = hourly_points(fixes)
    if not points:
        raise GlobeError(
            "no GPS positions to ask the globe about: give the pipeline a GPS "
            "source, or raw files with NMEA fixes"
        )
    days = sorted({p["time"][:10] for p in points})
    model = model_for(dt.date.fromisoformat(days[len(days) // 2]), settings)
    body: dict = {"points": points, "model": model}
    if settings.insitu:
        body["insitu"] = {}
    req = urllib.request.Request(
        f"{settings.url}/api/env/track?format=cchdo",
        data=json.dumps(body).encode(),
        headers={**_headers(settings), "Content-Type": "application/json"},
        method="POST",
    )
    path.write_bytes(_request(req))
    with xr.open_dataset(path) as casts:
        if not casts.sizes.get("N_PROF"):
            raise GlobeError(f"the globe returned no water columns for {len(points)} positions")
        kinds = dict.fromkeys(CAST_KINDS.values(), 0)
        if "cast_source" in casts:
            for flag, count in zip(
                *np.unique(casts["cast_source"].values, return_counts=True), strict=True
            ):
                kinds[CAST_KINDS.get(int(flag), f"kind {int(flag)}")] = int(count)
        else:
            kinds["model"] = int(casts.sizes["N_PROF"])
        source = str(casts.attrs.get("source", model))
    return TrackCasts(path=path, model=model, source=source, kinds=kinds)
