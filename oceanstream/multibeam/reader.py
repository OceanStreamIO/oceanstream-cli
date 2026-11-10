"""Read bottom detections from Kongsberg .all files with themachinethatgoesping."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .soundings import SoundingBatch

INSTALL_HINT = (
    "Reading Kongsberg .all files needs themachinethatgoesping. Install it with "
    "`pip install 'oceanstream[multibeam]'`, or, to leave out its Qt viewer dependencies, "
    "`pip install --no-deps themachinethatgoesping==0.37.1 nanobind-backend pytimeparse2` "
    "in an environment that already has numpy, pandas, scipy, matplotlib and rasterio."
)

SECONDS_PER_DAY = 86400


def library_version() -> str | None:
    """Installed themachinethatgoesping version, or None."""
    try:
        import themachinethatgoesping as tmtgp  # noqa: PLC0415
    except ImportError:
        return None
    return str(getattr(tmtgp, "__version__", "unknown"))


def _file_handler(files: Sequence[Path]) -> Any:
    try:
        from themachinethatgoesping.echosounders import (  # noqa: PLC0415
            kongsbergall,
        )
    except ImportError as exc:
        raise ImportError(INSTALL_HINT) from exc
    return kongsbergall.KongsbergAllFileHandler([str(f) for f in files], show_progress=False)


def day_number(day: str) -> int:
    """Days since 1970-01-01 of an ISO date (YYYY-MM-DD)."""
    return int(np.datetime64(day, "D").astype(np.int64))


def read_kongsberg_all(
    files: Sequence[Path],
    *,
    days: Sequence[str] | None = None,
    batch_pings: int = 5000,
) -> Iterator[SoundingBatch]:
    """Yield the valid bottom detections of the files, a few thousand pings at a time.

    Each ping's soundings come from its XYZ88 datagram. The vessel reference point's
    position at the ping comes from the file handler's navigation (target "0"), which
    interpolates the position datagrams. ``days`` keeps only pings on those UTC dates.
    """
    if not files:
        return
    handler = _file_handler(files)
    pings = handler.get_pings()
    keep_days = {day_number(d) for d in days} if days else None
    parts: dict[str, list[np.ndarray]] = {k: [] for k in _FIELDS}
    n_pings = 0
    for i in range(len(pings)):
        ping = pings[i]
        datagrams = ping.file_data.datagrams("XYZDatagram")
        if len(datagrams) == 0:
            continue
        xyz = datagrams[0]
        t = float(xyz.get_timestamp())
        if keep_days is not None and int(t // SECONDS_PER_DAY) not in keep_days:
            continue
        beams = xyz.beams
        depth = np.asarray(beams.get_depth_tensor(), dtype=np.float32)
        valid = (np.asarray(beams.get_detection_is_valid_tensor()) == 1) & np.isfinite(depth)
        n = int(valid.sum())
        if n == 0:
            continue
        ref = ping.get_geolocation("0")
        parts["ping_time"].append(np.full(n, np.datetime64(int(round(t * 1e9)), "ns")))
        parts["beam"].append(np.flatnonzero(valid).astype(np.uint16))
        parts["ref_latitude"].append(np.full(n, ref.latitude))
        parts["ref_longitude"].append(np.full(n, ref.longitude))
        parts["heading_deg"].append(np.full(n, xyz.get_heading_in_degrees()))
        parts["along_m"].append(np.asarray(beams.get_alongtrack_distance_tensor())[valid])
        parts["across_m"].append(np.asarray(beams.get_acrosstrack_distance_tensor())[valid])
        parts["depth_below_tx_m"].append(depth[valid])
        parts["transducer_depth_m"].append(np.full(n, xyz.get_transmit_transducer_depth()))
        parts["backscatter_db"].append(np.asarray(beams.get_backscatter_tensor())[valid])
        parts["beam_angle_deg"].append(
            np.asarray(beams.get_beam_incidence_angle_horizontal_plane_in_degrees_tensor())[valid]
        )
        parts["quality_factor"].append(np.asarray(beams.get_quality_factor_tensor())[valid])
        n_pings += 1
        if n_pings >= batch_pings:
            yield _batch(parts)
            parts = {k: [] for k in _FIELDS}
            n_pings = 0
    if n_pings:
        yield _batch(parts)


_DTYPES: dict[str, Any] = {
    "ping_time": "datetime64[ns]",
    "beam": np.uint16,
    "ref_latitude": np.float64,
    "ref_longitude": np.float64,
    "heading_deg": np.float64,
    "along_m": np.float32,
    "across_m": np.float32,
    "depth_below_tx_m": np.float32,
    "transducer_depth_m": np.float32,
    "backscatter_db": np.float32,
    "beam_angle_deg": np.float32,
    "quality_factor": np.uint8,
}
_FIELDS = tuple(_DTYPES)


def _batch(parts: dict[str, list[np.ndarray]]) -> SoundingBatch:
    return SoundingBatch(
        **{k: np.concatenate(v).astype(_DTYPES[k], copy=False) for k, v in parts.items()}
    )
