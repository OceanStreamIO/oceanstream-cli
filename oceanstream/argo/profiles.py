"""Parse Argovis v2 profile documents and apply Argo QC flags."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

#: Argo QC flags kept by ``qc="good"``: good, probably good, value changed, estimated.
ACCEPTED_QC = frozenset({1, 2, 5, 8})

#: Variables every core Argo profile carries.
CORE_VARIABLES = ("pressure", "temperature", "salinity")

#: Order of increasing processing: real-time, adjusted, delayed-mode.
_MODE_RANK = {"R": 0, "A": 1, "D": 2}


def parse_time(value: Any) -> datetime | None:
    """Parse an ISO timestamp to a naive UTC datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _float_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def variable_major(data: Any, keys: list[str]) -> list[list[Any]]:
    """Return one array per key from an Argovis ``data`` block.

    Argovis v2 sends variable-major data (``data[i]`` is the array of
    ``keys[i]``). Older responses and some proxies send one row per level;
    those are transposed. When both readings fit (as many levels as keys),
    the v2 layout wins.
    """
    if not data or not keys:
        return [[] for _ in keys]
    n = len(keys)
    if len(data) == n and all(isinstance(a, list) for a in data):
        lengths = {len(a) for a in data}
        if len(lengths) == 1:
            return [list(a) for a in data]
    if all(isinstance(row, list) and len(row) == n for row in data):
        return [[row[i] for row in data] for i in range(n)]
    raise ValueError(f"unrecognised Argovis data layout ({len(data)} arrays for {n} keys)")


def profile_network(doc: dict) -> str:
    """Classify a profile as ``bgc``, ``deep`` or ``core`` from its sources."""
    names: set[str] = set()
    for src in doc.get("source") or []:
        if isinstance(src, dict):
            names.update(str(s) for s in src.get("source") or [])
    if any("bgc" in s for s in names):
        return "bgc"
    if any("deep" in s for s in names):
        return "deep"
    return "core"


def least_processed_mode(
    modes: dict[str, str | None], variables: Iterable[str] = CORE_VARIABLES
) -> str | None:
    """Least-processed data mode (R < A < D) across the given variables."""
    ranked = [m for v in variables if (m := modes.get(v)) in _MODE_RANK]
    if not ranked:
        return None
    return min(ranked, key=lambda m: _MODE_RANK[m])


def parse_argovis_profile(doc: dict) -> dict:
    """Normalise one Argovis v2 profile document.

    Returns a dict with identity (``profile_id``, ``wmo_id``, ``cycle_number``),
    position and time (naive UTC, with Argo QC), ``measurements`` /
    ``qc_flags`` / ``units`` / ``data_modes`` keyed by variable name, and
    summary fields. BGC ``*_sfile`` copies of core variables only fill in when
    the core variable is absent.
    """
    profile_id = str(doc.get("_id") or "")
    meta_ids = doc.get("metadata") or []
    wmo_id = (
        str(meta_ids[0]).split("_")[0] if meta_ids else profile_id.split("_")[0]
    ) or profile_id

    coords = (doc.get("geolocation") or {}).get("coordinates") or [None, None]
    lon = _float_or_none(coords[0])
    lat = _float_or_none(coords[1] if len(coords) > 1 else None)

    info = list(doc.get("data_info") or []) + [None, None, None]
    keys: list[str] = list(info[0] or [])
    meta_cols: list[str] = list(info[1] or [])
    meta_rows: list[list[Any]] = list(info[2] or [])
    unit_idx = meta_cols.index("units") if "units" in meta_cols else None
    mode_idx = meta_cols.index("data_keys_mode") if "data_keys_mode" in meta_cols else None

    arrays = variable_major(doc.get("data"), keys)

    measurements: dict[str, list[float | None]] = {}
    qc_flags: dict[str, list[int | None]] = {}
    units: dict[str, str] = {}
    data_modes: dict[str, str] = {}

    # Core names first so *_sfile copies never overwrite them.
    order = sorted(
        range(len(keys)), key=lambda i: keys[i].endswith("_sfile") or "_sfile_" in keys[i]
    )
    for i in order:
        key = keys[i]
        arr = arrays[i]
        meta = meta_rows[i] if i < len(meta_rows) and isinstance(meta_rows[i], list) else []
        if key.endswith("_argoqc"):
            name = key[: -len("_argoqc")].replace("_sfile", "")
            if name not in qc_flags:
                qc_flags[name] = [_int_or_none(v) for v in arr]
            continue
        name = key.replace("_sfile", "")
        if name in measurements:
            continue
        measurements[name] = [_float_or_none(v) for v in arr]
        if unit_idx is not None and unit_idx < len(meta) and meta[unit_idx]:
            units[name] = str(meta[unit_idx])
        if mode_idx is not None and mode_idx < len(meta) and meta[mode_idx]:
            data_modes[name] = str(meta[mode_idx])

    pressure = [p for p in measurements.get("pressure", []) if p is not None]
    sources = [s for s in doc.get("source") or [] if isinstance(s, dict)]

    return {
        "profile_id": profile_id,
        "wmo_id": wmo_id,
        "cycle_number": _int_or_none(doc.get("cycle_number")),
        "profile_direction": doc.get("profile_direction"),
        "network": profile_network(doc),
        "time": parse_time(doc.get("timestamp")),
        "latitude": lat,
        "longitude": lon,
        "position_qc": _int_or_none(doc.get("geolocation_argoqc")),
        "time_qc": _int_or_none(doc.get("timestamp_argoqc")),
        "data_mode": least_processed_mode(data_modes),
        "n_levels": len(measurements.get("pressure", [])),
        "pressure_min": min(pressure) if pressure else None,
        "pressure_max": max(pressure) if pressure else None,
        "variables": [v for v in measurements if v != "pressure"],
        "measurements": measurements,
        "qc_flags": qc_flags,
        "units": units,
        "data_modes": data_modes,
        "vertical_sampling_scheme": doc.get("vertical_sampling_scheme"),
        "source_url": sources[0].get("url") if sources else None,
        "source_updated": parse_time(sources[0].get("date_updated")) if sources else None,
    }


def apply_qc(
    measurements: dict[str, list[float | None]],
    qc_flags: dict[str, list[int | None]],
    accepted: frozenset[int] = ACCEPTED_QC,
) -> tuple[dict[str, list[float | None]], int]:
    """Blank values whose QC flag is not accepted.

    A level with rejected pressure is blanked for every variable. Variables
    without QC flags pass through. Returns ``(masked, n_rejected_values)``.
    """
    n = len(measurements.get("pressure", []))
    bad_pressure = [False] * n
    p_qc = qc_flags.get("pressure")
    if p_qc:
        bad_pressure = [(q is not None and q not in accepted) for q in p_qc[:n]]
        bad_pressure += [False] * max(0, n - len(p_qc))

    masked: dict[str, list[float | None]] = {}
    rejected = 0
    for name, values in measurements.items():
        flags = qc_flags.get(name)
        out: list[float | None] = []
        for i, v in enumerate(values):
            reject = i < n and bad_pressure[i]
            if flags is not None and i < len(flags) and flags[i] is not None:
                reject = reject or flags[i] not in accepted
            if reject and v is not None:
                rejected += 1
            out.append(None if reject else v)
        masked[name] = out
    return masked, rejected
