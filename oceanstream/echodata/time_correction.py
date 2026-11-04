"""Correct a recorder clock that was set wrong.

Some deployments record every timestamp off by a fixed amount: the 2019 SWFSC
Saildrones wrote their EK80 files 7 h fast (UTC+7), so their ping times and file
names disagree with the UTC navigation by 7 h. :func:`shift_echodata_times` moves
every timestamp of a converted file (ping times, NMEA and motion times, the
environment and vendor times) by the same offset, so the data and any track
in UTC line up.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def shift_datetime(dt: datetime | None, seconds: float) -> datetime | None:
    """*dt* plus *seconds* (``None`` stays ``None``)."""
    return dt + timedelta(seconds=seconds) if dt is not None and seconds else dt


def shift_dataset_times(ds: Any, seconds: float) -> Any:
    """A copy of *ds* with every datetime64 coordinate and variable moved by *seconds*."""
    import numpy as np  # noqa: PLC0415

    if not seconds:
        return ds
    delta = np.timedelta64(int(round(seconds * 1e9)), "ns")
    out = ds.copy()
    for name in list(out.coords) + list(out.data_vars):
        var = out[name]
        if np.issubdtype(var.dtype, np.datetime64):
            attrs, encoding = var.attrs, var.encoding
            if name in out.coords:
                out = out.assign_coords({name: var + delta})
            else:
                out[name] = var + delta
            out[name].attrs, out[name].encoding = attrs, encoding
    out.attrs["oceanstream_time_offset_s"] = seconds
    return out


def shift_echodata_times(echodata: Any, seconds: float) -> Any:
    """Move every timestamp in every group of an echopype ``EchoData`` by *seconds*."""
    if not seconds:
        return echodata
    for group in echodata.group_paths:
        ds = echodata[group]
        if ds is None or group == "Top-level":
            continue
        echodata[group] = shift_dataset_times(ds, seconds)
    return echodata
