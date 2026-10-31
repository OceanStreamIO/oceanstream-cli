"""Campaign-bound Argo datasets: query, vessel-track match-ups, GeoParquet output.

``build_argo_dataset`` collects every Argo profile inside a bounding box and
time window from Argovis, optionally measures each profile's distance and time
offset to a vessel track, and returns an :class:`ArgoDataset` that writes
GeoParquet in one of two layouts:

``profiles``
    One row per profile. Each variable is a list column (``temperature``)
    with a matching list of Argo QC flags (``temperature_qc``), indexed by
    level. This maps one-to-one onto a profiles table in PostGIS.
``levels``
    One row per profile and depth level, with scalar columns. Suited to
    pandas, DuckDB and GIS tools that do not read list columns.
"""

from __future__ import annotations

import json
import logging
import math
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .. import __version__
from ..sources.argovis import DEFAULT_BASE_URL, ArgovisClient, fetch_profiles
from .profiles import ACCEPTED_QC, CORE_VARIABLES, apply_qc, parse_argovis_profile

logger = logging.getLogger(__name__)

ARGO_CITATION = (
    "Argo (2000). Argo float data and metadata from Global Data Assembly Centre "
    "(Argo GDAC). SEANOE. https://doi.org/10.17882/42182"
)
ARGO_DOI = "10.17882/42182"
ARGO_LICENSE = "CC-BY-4.0"
ARGO_ACKNOWLEDGEMENT = (
    "These data were collected and made freely available by the International Argo "
    "Program and the national programs that contribute to it (https://argo.ucsd.edu, "
    "https://www.ocean-ops.org). The Argo Program is part of the Global Ocean Observing System."
)
ARGOVIS_CITATION = (
    "Tucker, T., Giglio, D., Scanderbeg, M., Shen, S. S. P. (2020). Argovis: A Web "
    "Application for Fast Delivery, Visualization, and Analysis of Argo Data. Journal of "
    "Atmospheric and Oceanic Technology, 37(3), 401-416. https://doi.org/10.1175/JTECH-D-19-0041.1"
)

LAYOUTS = ("profiles", "levels")
QC_MODES = ("raw", "good")

#: Cap on track points used for the nearest-position search (10 s fixes
#: decimated to this many points stay within a few hundred metres).
_MAX_TRACK_POINTS = 50_000

_TIME_COLUMNS = ("time", "timestamp", "datetime", "date_time")
_LAT_COLUMNS = ("latitude", "lat")
_LON_COLUMNS = ("longitude", "lon", "long")

_TS = pa.timestamp("us", tz="UTC")


def _iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ─────────────────────────────────────────────────────────────────────────────
# Bounds and vessel track
# ─────────────────────────────────────────────────────────────────────────────


def expand_bbox(bbox: list[float], buffer_km: float) -> list[float]:
    """Grow ``[west, south, east, north]`` by ``buffer_km`` on every side."""
    west, south, east, north = bbox
    if buffer_km <= 0:
        return [west, south, east, north]
    dlat = buffer_km / 111.32
    mid = math.radians((south + north) / 2)
    dlon = buffer_km / (111.32 * max(math.cos(mid), 0.05))
    return [
        max(-180.0, west - dlon),
        max(-90.0, south - dlat),
        min(180.0, east + dlon),
        min(90.0, north + dlat),
    ]


def haversine_km(lat1: float, lon1: float, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Great-circle distance in km from one point to arrays of points."""
    lat1r, lon1r = math.radians(lat1), math.radians(lon1)
    lat2r, lon2r = np.radians(lat2), np.radians(lon2)
    a = (
        np.sin((lat2r - lat1r) / 2) ** 2
        + math.cos(lat1r) * np.cos(lat2r) * np.sin((lon2r - lon1r) / 2) ** 2
    )
    return 6371.0088 * 2 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


@dataclass
class Track:
    """Vessel positions sorted by time (naive UTC ``datetime64[us]``)."""

    time: np.ndarray
    latitude: np.ndarray
    longitude: np.ndarray
    source: str | None = None

    def __len__(self) -> int:
        return int(self.latitude.size)


def _pick(names: list[str], candidates: tuple[str, ...]) -> str | None:
    lower = {n.lower(): n for n in names}
    return next((lower[c] for c in candidates if c in lower), None)


def load_track(path: str | Path) -> Track:
    """Read a vessel track from GeoParquet (a file or a partitioned dataset) or CSV.

    Needs latitude and longitude columns (``latitude``/``lat``,
    ``longitude``/``lon``); a ``time`` column is optional and enables the
    time offset. ``oceanstream process geotrack`` output works as-is.
    """
    path = Path(path)
    if path.is_dir():
        files = sorted(f for f in path.rglob("*.parquet") if "stac" not in f.parts)
        if not files:
            raise FileNotFoundError(f"no Parquet files under {path}")
    elif path.exists():
        files = [path]
    else:
        raise FileNotFoundError(path)

    if files[0].suffix.lower() in (".csv", ".txt"):
        names = list(pd.read_csv(files[0], nrows=0).columns)
    else:
        names = pq.read_schema(files[0]).names
    t_col = _pick(names, _TIME_COLUMNS)
    lat_col = _pick(names, _LAT_COLUMNS)
    lon_col = _pick(names, _LON_COLUMNS)
    if not lat_col or not lon_col:
        raise ValueError(f"{path}: no latitude/longitude columns (found {', '.join(names[:12])})")
    cols = [c for c in (t_col, lat_col, lon_col) if c]

    if files[0].suffix.lower() in (".csv", ".txt"):
        df = pd.read_csv(files[0], usecols=cols)
    else:
        tables = [pq.read_table(f, columns=cols).to_pandas() for f in files]
        df = pd.concat(tables, ignore_index=True)

    lat = pd.to_numeric(df[lat_col], errors="coerce")
    lon = pd.to_numeric(df[lon_col], errors="coerce")
    if t_col:
        times = pd.to_datetime(df[t_col], utc=True, errors="coerce").dt.tz_convert(None)
    else:
        times = pd.Series(pd.NaT, index=df.index, dtype="datetime64[us]")
    ok = lat.notna() & lon.notna() & lat.between(-90, 90) & lon.between(-180, 180)
    frame = pd.DataFrame({"time": times[ok], "lat": lat[ok], "lon": lon[ok]}).sort_values("time")
    return Track(
        time=frame["time"].to_numpy(dtype="datetime64[us]"),
        latitude=frame["lat"].to_numpy(dtype=float),
        longitude=frame["lon"].to_numpy(dtype=float),
        source=str(path),
    )


def track_matchups(profiles: list[dict], track: Track | None) -> list[dict]:
    """Closest vessel-track position for each profile.

    Returns one dict per profile (same order) with ``nearest_track_km``,
    ``nearest_track_time``, ``nearest_track_lat``, ``nearest_track_lon`` and
    ``time_offset_hours`` (profile time minus vessel time, positive when the
    float surfaced after the vessel passed). Empty dicts without a track.
    """
    if track is None or len(track) == 0:
        return [{} for _ in profiles]
    tt, tla, tlo = track.time, track.latitude, track.longitude
    if tla.size > _MAX_TRACK_POINTS:
        stride = math.ceil(tla.size / _MAX_TRACK_POINTS)
        tt, tla, tlo = tt[::stride], tla[::stride], tlo[::stride]

    out: list[dict] = []
    for p in profiles:
        lat, lon = p.get("latitude"), p.get("longitude")
        if lat is None or lon is None:
            out.append({})
            continue
        d = haversine_km(lat, lon, tla, tlo)
        i = int(np.argmin(d))
        t = tt[i]
        t_py = None if np.isnat(t) else t.astype("datetime64[us]").astype(datetime)
        offset = None
        if p.get("time") is not None and t_py is not None:
            offset = round((p["time"] - t_py).total_seconds() / 3600.0, 3)
        out.append({
            "nearest_track_km": round(float(d[i]), 3),
            "nearest_track_time": t_py,
            "nearest_track_lat": float(tla[i]),
            "nearest_track_lon": float(tlo[i]),
            "time_offset_hours": offset,
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Dataset
# ─────────────────────────────────────────────────────────────────────────────


def _summary_stats(values: list[float | None]) -> dict | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    arr = np.array(vals)
    return {
        "min": round(float(arr.min()), 2),
        "median": round(float(np.median(arr)), 2),
        "max": round(float(arr.max()), 2),
    }


def _wkb_point(lon: float, lat: float) -> bytes:
    return struct.pack("<BIdd", 1, 1, lon, lat)


def _at(values: list | None, i: int) -> Any:
    return values[i] if values is not None and i < len(values) else None


_MATCHUP_FIELDS = [
    pa.field("nearest_track_km", pa.float64()),
    pa.field("nearest_track_time", _TS),
    pa.field("nearest_track_lat", pa.float64()),
    pa.field("nearest_track_lon", pa.float64()),
    pa.field("time_offset_hours", pa.float64()),
]


@dataclass
class ArgoDataset:
    """Argo profiles for one campaign extent, with provenance.

    ``profiles`` holds dicts from :func:`parse_argovis_profile` plus the
    track match-up fields; ``provenance`` records the query, counts and the
    Argo citation and licence.
    """

    profiles: list[dict]
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def variables(self) -> list[str]:
        """Core variables first, then every other measured variable A-Z."""
        seen = {v for p in self.profiles for v in p["measurements"]}
        return list(CORE_VARIABLES) + sorted(seen - set(CORE_VARIABLES))

    @property
    def units(self) -> dict[str, str]:
        units: dict[str, str] = {}
        for p in self.profiles:
            for name, unit in p["units"].items():
                units.setdefault(name, unit)
        units.update({"nearest_track_km": "km", "time_offset_hours": "h"})
        return units

    def _values(self, p: dict, qc: str) -> tuple[dict[str, list[float | None]], int]:
        if qc == "good":
            return apply_qc(p["measurements"], p["qc_flags"])
        return p["measurements"], 0

    def to_table(self, layout: str = "profiles", qc: str = "raw") -> pa.Table:
        """Arrow table with GeoParquet ``geo`` metadata and provenance."""
        if layout not in LAYOUTS:
            raise ValueError(f"layout must be one of {LAYOUTS}, got {layout!r}")
        if qc not in QC_MODES:
            raise ValueError(f"qc must be one of {QC_MODES}, got {qc!r}")
        if layout == "profiles":
            table, rejected = self._profiles_table(qc)
        else:
            table, rejected = self._levels_table(qc)
        return table.replace_schema_metadata(self._file_metadata(layout, qc, rejected))

    def _profiles_table(self, qc: str) -> tuple[pa.Table, int]:
        variables = self.variables
        cols: dict[str, list] = {name: [] for name in (
            "campaign_id", "profile_id", "wmo_id", "cycle_number", "profile_direction",
            "network", "time", "latitude", "longitude", "position_qc", "time_qc",
            "data_mode", "n_levels", "pressure_min", "pressure_max", "variables",
            "data_modes", "vertical_sampling_scheme", "source_url", "source_updated",
            *(f.name for f in _MATCHUP_FIELDS),
        )}
        var_cols: dict[str, list] = {}
        for v in variables:
            var_cols[v] = []
            var_cols[f"{v}_qc"] = []
        geometry: list[bytes] = []
        rejected = 0

        campaign_id = self.provenance.get("campaign_id")
        for p in self.profiles:
            values, n_rej = self._values(p, qc)
            rejected += n_rej
            row = {
                **p,
                "campaign_id": campaign_id,
                "data_modes": json.dumps(p["data_modes"], sort_keys=True),
            }
            for name, col in cols.items():
                col.append(row.get(name))
            for v in variables:
                var_cols[v].append(values.get(v))
                var_cols[f"{v}_qc"].append(p["qc_flags"].get(v))
            geometry.append(_wkb_point(p["longitude"], p["latitude"]))

        schema = [
            pa.field("campaign_id", pa.string()),
            pa.field("profile_id", pa.string()),
            pa.field("wmo_id", pa.string()),
            pa.field("cycle_number", pa.int32()),
            pa.field("profile_direction", pa.string()),
            pa.field("network", pa.string()),
            pa.field("time", _TS),
            pa.field("latitude", pa.float64()),
            pa.field("longitude", pa.float64()),
            pa.field("position_qc", pa.int8()),
            pa.field("time_qc", pa.int8()),
            pa.field("data_mode", pa.string()),
            pa.field("n_levels", pa.int32()),
            pa.field("pressure_min", pa.float64()),
            pa.field("pressure_max", pa.float64()),
            pa.field("variables", pa.list_(pa.string())),
            pa.field("data_modes", pa.string()),
            pa.field("vertical_sampling_scheme", pa.string()),
            pa.field("source_url", pa.string()),
            pa.field("source_updated", _TS),
            *_MATCHUP_FIELDS,
        ]
        for v in variables:
            schema.append(pa.field(v, pa.list_(pa.float64())))
            schema.append(pa.field(f"{v}_qc", pa.list_(pa.int8())))
        schema.append(pa.field("geometry", pa.binary()))

        data = {**cols, **var_cols, "geometry": geometry}
        arrays = [pa.array(data[f.name], type=f.type) for f in schema]
        return pa.Table.from_arrays(arrays, schema=pa.schema(schema)), rejected

    def _levels_table(self, qc: str) -> tuple[pa.Table, int]:
        variables = self.variables
        profile_cols = (
            "profile_id", "wmo_id", "cycle_number", "time", "latitude", "longitude",
            "position_qc", "network", "data_mode", "nearest_track_km", "time_offset_hours",
        )
        rows: dict[str, list] = {name: [] for name in ("campaign_id", *profile_cols, "level")}
        for v in variables:
            rows[v] = []
            rows[f"{v}_qc"] = []
        rows["geometry"] = []
        rejected = 0

        campaign_id = self.provenance.get("campaign_id")
        for p in self.profiles:
            values, n_rej = self._values(p, qc)
            rejected += n_rej
            pressure = values.get("pressure") or []
            point = _wkb_point(p["longitude"], p["latitude"])
            for i in range(p["n_levels"]):
                if qc == "good" and (i >= len(pressure) or pressure[i] is None):
                    continue
                rows["campaign_id"].append(campaign_id)
                for name in profile_cols:
                    rows[name].append(p.get(name))
                rows["level"].append(i)
                for v in variables:
                    rows[v].append(_at(values.get(v), i))
                    rows[f"{v}_qc"].append(_at(p["qc_flags"].get(v), i))
                rows["geometry"].append(point)

        types = {
            "campaign_id": pa.string(), "profile_id": pa.string(), "wmo_id": pa.string(),
            "cycle_number": pa.int32(), "time": _TS, "latitude": pa.float64(),
            "longitude": pa.float64(), "position_qc": pa.int8(), "network": pa.string(),
            "data_mode": pa.string(), "nearest_track_km": pa.float64(),
            "time_offset_hours": pa.float64(), "level": pa.int32(), "geometry": pa.binary(),
        }
        for v in variables:
            types[v] = pa.float64()
            types[f"{v}_qc"] = pa.int8()
        schema = pa.schema([pa.field(name, types[name]) for name in rows])
        arrays = [pa.array(rows[f.name], type=f.type) for f in schema]
        return pa.Table.from_arrays(arrays, schema=schema), rejected

    def _file_metadata(self, layout: str, qc: str, rejected: int) -> dict[bytes, bytes]:
        accepted = ", ".join(str(f) for f in sorted(ACCEPTED_QC))
        if qc == "good":
            policy = (
                f"Values whose Argo QC flag is not one of {accepted} are null; a rejected "
                "pressure blanks the whole level. Flags are kept in the <variable>_qc columns."
            )
        else:
            policy = (
                "Values are as published. Argo QC flags are in the <variable>_qc columns; "
                f"flags {accepted} are usually accepted."
            )
        geo_column: dict[str, Any] = {"encoding": "WKB", "geometry_types": ["Point"]}
        if self.profiles:
            lons = [p["longitude"] for p in self.profiles]
            lats = [p["latitude"] for p in self.profiles]
            geo_column["bbox"] = [min(lons), min(lats), max(lons), max(lats)]
        geo = {
            "version": "1.1.0",
            "primary_column": "geometry",
            "columns": {"geometry": geo_column},
        }
        argo = {
            **self.provenance,
            "layout": layout,
            "qc": qc,
            "qc_policy": policy,
            "rejected_values": rejected if qc == "good" else None,
        }
        return {
            b"geo": json.dumps(geo).encode(),
            b"oceanstream:argo": json.dumps(argo).encode(),
            b"oceanstream:units": json.dumps(self.units).encode(),
        }

    def to_dataframe(self, layout: str = "profiles", qc: str = "raw") -> pd.DataFrame:
        """The table as pandas (``geometry`` stays WKB bytes)."""
        return self.to_table(layout, qc).to_pandas()

    def to_geoparquet(self, path: str | Path, layout: str = "profiles", qc: str = "raw") -> Path:
        """Write a single GeoParquet 1.1 file and return its path."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(self.to_table(layout, qc), path)
        return path


def build_argo_dataset(  # noqa: PLR0913
    bbox: list[float],
    start: datetime,
    end: datetime,
    *,
    buffer_km: float = 0.0,
    buffer_days: float = 0.0,
    track: Track | None = None,
    max_track_km: float | None = None,
    chunk_days: float = 10,
    client: ArgovisClient | None = None,
    campaign_id: str | None = None,
    progress: Callable[[datetime, datetime, int], None] | None = None,
) -> ArgoDataset:
    """Argo profiles inside ``bbox`` between ``start`` and ``end`` (naive UTC).

    ``buffer_km`` and ``buffer_days`` widen the query. With a ``track``, every
    profile gets its distance and time offset to the closest vessel position,
    and ``max_track_km`` drops profiles farther than that.
    """
    if max_track_km is not None and (track is None or len(track) == 0):
        raise ValueError("max_track_km needs a vessel track")
    query_bbox = expand_bbox(list(bbox), buffer_km)
    pad = timedelta(days=buffer_days)
    t0, t1 = start - pad, end + pad
    client = client or ArgovisClient()

    docs = fetch_profiles(
        query_bbox, t0, t1, chunk_days=chunk_days, client=client, progress=progress
    )

    profiles = []
    for doc in docs:
        try:
            p = parse_argovis_profile(doc)
        except ValueError as exc:
            logger.warning("skipping Argo profile %s: %s", doc.get("_id"), exc)
            continue
        if p["time"] is None or p["latitude"] is None or p["longitude"] is None:
            continue
        if p["n_levels"] == 0 or not (t0 <= p["time"] <= t1):
            continue
        profiles.append(p)

    for p, m in zip(profiles, track_matchups(profiles, track), strict=True):
        p.update({f.name: None for f in _MATCHUP_FIELDS})
        p.update(m)
    if max_track_km is not None:
        profiles = [
            p for p in profiles
            if p["nearest_track_km"] is not None and p["nearest_track_km"] <= max_track_km
        ]
    profiles.sort(key=lambda p: (p["time"], p["profile_id"]))

    networks: dict[str, int] = {}
    modes: dict[str, int] = {}
    for p in profiles:
        networks[p["network"]] = networks.get(p["network"], 0) + 1
        modes[p["data_mode"] or "?"] = modes.get(p["data_mode"] or "?", 0) + 1

    provenance = {
        "campaign_id": campaign_id,
        "source": "Argovis API v2",
        "source_url": f"{getattr(client, 'base_url', DEFAULT_BASE_URL)}/argo",
        "retrieved_at": _iso(_utcnow()),
        "bbox": [round(v, 6) for v in query_bbox],
        "time_start": _iso(t0),
        "time_end": _iso(t1),
        "buffer_km": buffer_km,
        "buffer_days": buffer_days,
        "max_track_km": max_track_km,
        "n_profiles": len(profiles),
        "n_floats": len({p["wmo_id"] for p in profiles}),
        "networks": networks,
        "data_modes": modes,
        "variables": sorted({v for p in profiles for v in p["variables"]}),
        "matched_to_track": track is not None and len(track) > 0,
        "track_source": track.source if track is not None else None,
        "track_points": len(track) if track is not None else 0,
        "nearest_track_km": _summary_stats([p["nearest_track_km"] for p in profiles]),
        "abs_time_offset_hours": _summary_stats(
            [abs(p["time_offset_hours"]) for p in profiles if p["time_offset_hours"] is not None]
        ),
        "citation": ARGO_CITATION,
        "doi": ARGO_DOI,
        "license": ARGO_LICENSE,
        "acknowledgement": ARGO_ACKNOWLEDGEMENT,
        "service_citation": ARGOVIS_CITATION,
        "generator": f"oceanstream {__version__}",
    }
    return ArgoDataset(profiles=profiles, provenance=provenance)
